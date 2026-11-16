"""M2 编排：别名合并 → 取值归一 → 窗口匹配。

对外只暴露 normalize() 与 match()，别的模块不 import 本模块的规则细节。
"""
from __future__ import annotations

from datetime import datetime

from . import alias, coerce, window
from .contract import CHANNEL_CODES


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def normalize(settings, raw: dict) -> dict:
    """把原始字段变成带类型与错误码的行。

    raw: {"touch": {"header": [...], "rows": [...]}, "order": ..., "alias": ...}
    返回：{"touches", "orders", "alias", "quarantined", "file_rows"}
    """
    touch = raw["touch"]
    order = raw["order"]
    alias_frame = raw["alias"]

    alias_rows = []
    for row in alias_frame["rows"]:
        if row["kind"] in {"blank", "dup_header"}:
            continue
        alias_rows.append({
            "row_no": row["row_no"],
            "raw_line": row["raw_line"],
            "kind": row["kind"],
            "alias_id": _pick(row["fields"], alias_frame["header"], "alias_id", "alias"),
            "canonical_id": _pick(row["fields"], alias_frame["header"], "canonical_id", "alias"),
        })
    alias_result = alias.resolve(alias_rows)
    mapping = alias_result["mapping"]

    quarantined = []
    for row in alias_result["bad_rows"]:
        quarantined.append({
            "source": "alias", "row_no": row["row_no"], "raw_line": row["raw_line"],
            "reason": "ALIAS_BAD_ROW",
            "raw_value": f"{row.get('alias_id') or ''}|{row.get('canonical_id') or ''}",
        })

    touches = []
    for row in touch["rows"]:
        if row["kind"] in {"blank", "dup_header"}:
            continue
        fields = row["fields"]
        touch_id = _pick(fields, touch["header"], "touch_id", "touch")
        user_id = _pick(fields, touch["header"], "user_id", "touch")
        device_id = _pick(fields, touch["header"], "device_id", "touch")
        channel_raw = _pick(fields, touch["header"], "channel", "touch")
        event_raw = _pick(fields, touch["header"], "event_type", "touch")
        event_ts = coerce.parse_ts(_pick(fields, touch["header"], "event_time", "touch"))
        ingested_ts = coerce.parse_ts(_pick(fields, touch["header"], "ingested_at", "touch"))
        cost = coerce.parse_amount(_pick(fields, touch["header"], "cost", "touch"))
        errors = []
        if row["kind"] in {"mismatch", "truncated"}:
            errors.append("TD_COL_COUNT")
        if event_ts["status"] != "ok":
            errors.append("TD_TIME_UNPARSABLE")
        if cost["status"] == "invalid":
            errors.append("TD_COST_INVALID")
        touches.append({
            "row_no": row["row_no"],
            "raw_line": row["raw_line"],
            "touch_id": (touch_id or "").strip(),
            "user_id": (user_id or "").strip(),
            "device_id": (device_id or "").strip(),
            "canonical_user": alias.rep(mapping, user_id) or "",
            "channel": coerce.parse_channel(channel_raw, CHANNEL_CODES),
            "channel_raw": (channel_raw or "").strip(),
            "event_type": coerce.parse_event_type(event_raw),
            "event_type_raw": (event_raw or "").strip(),
            "event_time_utc": coerce.to_utc_iso(event_ts["utc"]),
            "ingested_at_utc": coerce.to_utc_iso(ingested_ts["utc"]),
            "cost_cents": cost["cents"],
            "tz_kind": event_ts["kind"],
            "note": row["kind"],
            "errors": errors,
            "status": "OK",
            "reason": None,
            "late": False,
        })

    orders = []
    for row in order["rows"]:
        if row["kind"] in {"blank", "dup_header"}:
            continue
        fields = row["fields"]
        order_id = _pick(fields, order["header"], "order_id", "order")
        user_id = _pick(fields, order["header"], "user_id", "order")
        unit = _pick(fields, order["header"], "unit", "order") or ""
        amount = coerce.parse_amount(_pick(fields, order["header"], "gmv", "order"), unit)
        order_ts = coerce.parse_ts(_pick(fields, order["header"], "order_time", "order"))
        errors = []
        if row["kind"] in {"mismatch", "truncated"}:
            errors.append("TD_COL_COUNT")
        if order_ts["status"] != "ok":
            errors.append("OD_TIME_UNPARSABLE")
        if amount["status"] != "ok":
            errors.append("OD_GMV_INVALID")
        orders.append({
            "row_no": row["row_no"],
            "raw_line": row["raw_line"],
            "order_id": (order_id or "").strip(),
            "user_id": (user_id or "").strip(),
            "canonical_user": alias.rep(mapping, user_id) or "",
            "gmv_cents": amount["cents"],
            "order_time_utc": coerce.to_utc_iso(order_ts["utc"]),
            "unit_raw": (unit or "元").strip(),
            "errors": errors,
            "status": "OK",
            "reason": None,
        })

    return {
        "touches": touches,
        "orders": orders,
        "alias": alias_result,
        "quarantined": quarantined,
    }


def match(settings, orders: list[dict], touches: list[dict]) -> dict:
    """按主账号 + 半开窗口把触点匹配到订单。

    返回 {"matches": {order_id: [touch row_no...]}, "status": {row_no: {...}}}
    """
    by_user: dict[str, list[dict]] = {}
    for touch in touches:
        by_user.setdefault(touch["canonical_user"], []).append(touch)

    matches: dict[str, list[dict]] = {}
    status: dict[int, dict] = {
        touch["row_no"]: {"status": "DROPPED", "reason": "OUT_OF_WINDOW", "late": False}
        for touch in touches
    }
    for order in orders:
        order_time = _dt(order["order_time_utc"])
        if order_time is None:
            continue
        hits = []
        for touch in by_user.get(order["canonical_user"], []):
            moment = _dt(touch["event_time_utc"])
            if moment is None:
                continue
            start, end = window.bounds(order_time, touch["event_type"], settings)
            if not window.contains(start, end, moment):
                continue
            late = window.is_late(_dt(touch["ingested_at_utc"]), order_time)
            hits.append({"touch": touch, "late": late})
            current = status[touch["row_no"]]
            if late:
                current.update({"status": "LATE", "late": True})
                if current["reason"] == "OUT_OF_WINDOW":
                    current["reason"] = None
            elif current["status"] != "LATE":
                current.update({"status": "COUNTED", "reason": None})
        hits.sort(key=lambda item: (item["touch"]["event_time_utc"], item["touch"]["row_no"]))
        matches[order["order_id"]] = hits

    return {"matches": matches, "status": status}


def _pick(fields: list[str], header: list[str], column: str, kind: str) -> str | None:
    from ..m1_ingest import sniff
    mapping = sniff.map_header(header, kind)
    return sniff.value(fields, mapping, column)
