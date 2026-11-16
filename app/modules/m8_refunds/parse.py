"""退款 CSV 解析与逐行归一：金额到整数分、时间到 UTC、内容指纹。

输入列：refund_id,order_id,refund,refund_time,unit
解析阶段只做形态归一与行内校验，业务校验（订单是否存在、是否超额）在 service 里
结合 store 里的既有证据完成，保证退款始终挂在已审批的归因结果之上。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ... import reasons
from ..m1_ingest import reader
from ..m2_window import coerce

EXPECTED_COLUMNS = ("refund_id", "order_id", "refund", "refund_time", "unit")
COLUMN_COUNT = len(EXPECTED_COLUMNS)


@dataclass
class RefundLine:
    row_no: int
    raw_line: int
    kind: str = "data"                 # data | blank | dup_header | truncated | mismatch
    fields: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_text(text: str) -> dict:
    """读入退款文本，返回表头与数据行（含空行、截断、重复表头标记）。"""
    return reader.read_all(text, COLUMN_COUNT)


def _content_digest(refund_id: str, order_id: str, cents: int,
                    refund_time_utc: str | None) -> str:
    blob = json.dumps(
        {"refund_id": refund_id, "order_id": order_id, "refund_cents": int(cents),
         "refund_time_utc": refund_time_utc},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_row(row: dict) -> dict:
    """把一行原始字段归一成退款证据行；行内问题直接带原因码。"""
    fields = list(row.get("fields", []))
    padded = fields + [""] * (COLUMN_COUNT - len(fields))
    refund_id = coerce.normalize_text(padded[0])
    order_id = coerce.normalize_text(padded[1])
    amount_raw = padded[2]
    time_raw = padded[3]
    unit = coerce.normalize_text(padded[4]) or "元"

    codes: list[str] = []
    if not refund_id or not order_id:
        # 编号缺失的行无法挂接原订单，按行形态不可处理归到 RF_AMOUNT_INVALID
        codes.append("RF_AMOUNT_INVALID")
    amount = coerce.parse_amount(amount_raw, unit)
    if amount["status"] != "ok" or int(amount["cents"] or 0) <= 0:
        codes.append("RF_AMOUNT_INVALID")
    parsed_time = coerce.parse_ts(time_raw)
    if parsed_time["status"] != "ok":
        codes.append("RF_TIME_UNPARSABLE")

    cents = int(amount["cents"] or 0) if amount["status"] == "ok" else 0
    utc_iso = coerce.to_utc_iso(parsed_time["utc"]) if parsed_time["status"] == "ok" else None
    code = reasons.pick(codes)
    return {
        "row_no": int(row["row_no"]),
        "raw_line": int(row.get("raw_line", 0)),
        "refund_id": refund_id,
        "order_id": order_id,
        "refund_cents": cents,
        "refund_time_utc": utc_iso,
        "status": "QUARANTINED" if code else "PENDING",
        "reason": code,
        "applied": False,
        "content_digest": _content_digest(refund_id, order_id, cents, utc_iso),
    }


def normalize(parsed: dict) -> dict:
    """整批归一：数据行转证据行，空行/重复表头/截断行计数后剔除。"""
    rows: list[dict] = []
    blanks = dup_headers = truncated = mismatches = 0
    for raw in parsed["rows"]:
        kind = raw.kind
        if kind == "blank":
            blanks += 1
            continue
        if kind == "dup_header":
            dup_headers += 1
            continue
        if kind in {"truncated", "mismatch"}:
            truncated += 1 if kind == "truncated" else 0
            mismatches += 1 if kind == "mismatch" else 0
            rows.append({
                "row_no": int(raw.row_no), "raw_line": int(raw.raw_line),
                "refund_id": "", "order_id": "", "refund_cents": 0,
                "refund_time_utc": None, "status": "QUARANTINED",
                "reason": "RF_AMOUNT_INVALID", "applied": False,
                "content_digest": _content_digest("", "", 0, None),
                "notes": list(raw.notes),
            })
            continue
        rows.append(normalize_row({"row_no": raw.row_no, "raw_line": raw.raw_line,
                                   "fields": raw.fields}))
    # 数据行重新连续编号，物理行号保留
    for index, row in enumerate(rows, start=1):
        row["row_no"] = index
    return {
        "header": parsed["header"],
        "rows": rows,
        "blanks": blanks,
        "dup_headers": dup_headers,
        "truncated": truncated,
        "mismatches": mismatches,
    }


def batch_digest(rows: list[dict]) -> str:
    """整批内容指纹：归一后的行指纹参与哈希，同内容重试得到同一个批次指纹。"""
    material = [row["content_digest"] for row in sorted(rows, key=lambda item: item["row_no"])]
    blob = json.dumps(material, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
