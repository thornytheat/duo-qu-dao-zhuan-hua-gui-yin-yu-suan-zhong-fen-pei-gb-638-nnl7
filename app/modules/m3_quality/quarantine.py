"""隔离区：原因码分布、CSV 导出、四账对账。
"""
from __future__ import annotations

import csv
import io

from ... import reasons


def record(source: str, row: dict, code: str, raw_value: str) -> dict:
    return {
        "source": source,
        "row_no": row["row_no"],
        "raw_line": row["raw_line"],
        "reason": code,
        "raw_value": raw_value,
    }


def raw_of(kind: str, row: dict) -> str:
    if kind == "touch":
        return "|".join(str(row.get(key) or "") for key in
                        ("touch_id", "user_id", "channel", "event_type",
                         "event_time_utc", "cost_cents"))
    if kind == "order":
        return "|".join(str(row.get(key) or "") for key in
                        ("order_id", "user_id", "gmv_cents", "order_time_utc"))
    return str(row.get("raw_value") or "")


def distribution(rows: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    total = sum(counts.values()) or 1
    return [
        {"code": code, "label": reasons.label(code), "level": reasons.level(code),
         "count": count, "share_pct": round(count * 100.0 / total, 1)}
        for code, count in sorted(counts.items(),
                                  key=lambda item: (-item[1], item[0]))
    ]


def export_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["来源", "行号", "物理行", "原因码", "原因", "原值"])
    for row in rows:
        writer.writerow([row["source"], row["row_no"], row["raw_line"],
                         row["reason"], reasons.label(row["reason"]),
                         row["raw_value"]])
    return buffer.getvalue()


def accounts(touches: list[dict], orders: list[dict], quarantined: list[dict]) -> dict:
    counted = sum(1 for row in touches if row["status"] == "COUNTED")
    late = sum(1 for row in touches if row["status"] == "LATE")
    dropped = sum(1 for row in touches if row["status"] == "DROPPED")
    out_of_window = sum(1 for row in touches
                        if row["status"] == "DROPPED" and row["reason"] == "OUT_OF_WINDOW")
    quarantined_touch = sum(1 for row in quarantined if row["source"] == "touchpoint")
    quarantined_order = sum(1 for row in quarantined if row["source"] == "order")
    order_ok = sum(1 for row in orders if row["status"] == "OK")
    return {
        "touch_input": len(touches),
        "touch_counted": counted,
        "touch_late": late,
        "touch_dropped": dropped,
        "touch_out_of_window": out_of_window,
        "touch_quarantined": quarantined_touch,
        "order_input": len(orders),
        "order_ok": order_ok,
        "order_quarantined": quarantined_order,
        "alias_quarantined": sum(1 for row in quarantined if row["source"] == "alias"),
        "balanced_touch": len(touches) == counted + late + dropped,
        "balanced_order": len(orders) == order_ok + quarantined_order,
    }
