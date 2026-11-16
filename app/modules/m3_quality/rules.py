"""质量规则：渠道白名单、事件类型、重复业务键冲突。

一行可能同时命中多条规则，取值由 reasons.pick() 按固定优先级决定，
与这里追加规则的书写顺序无关。
"""
from __future__ import annotations

from ..m2_window.contract import CHANNEL_CODES

EVENT_TYPES = {"click", "impression"}


def touch_codes(touch: dict, settings) -> list[str]:
    codes = list(touch["errors"])
    if touch["channel"] not in CHANNEL_CODES:
        codes.append("TD_CHANNEL_UNKNOWN")
    if touch["event_type"] not in EVENT_TYPES:
        codes.append("TD_EVENT_TYPE_UNKNOWN")
    if not touch["touch_id"]:
        codes.append("TD_COL_COUNT")
    return codes


def order_codes(order: dict) -> list[str]:
    codes = list(order["errors"])
    if not order["order_id"]:
        codes.append("TD_COL_COUNT")
    return codes


def duplicate_flags(kind: str, rows: list[dict]) -> dict:
    """按业务键分组：字段冲突 → 全部隔离；内容相同 → 保留首条，其余隔离。"""
    key_field = "touch_id" if kind == "touch" else "order_id"
    conflict_code = "TD_TOUCH_DUP_CONFLICT" if kind == "touch" else "OD_DUP_CONFLICT"
    identical_code = "TD_TOUCH_DUP_IDENTICAL" if kind == "touch" else "OD_DUP_IDENTICAL"
    signature_fields = (("event_time_utc", "channel", "cost_cents", "event_type")
                        if kind == "touch" else ("gmv_cents", "order_time_utc"))

    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = (row.get(key_field) or "").strip()
        if key:
            groups.setdefault(key, []).append(row)

    conflicts: set[int] = set()
    identical_drops: set[int] = set()
    for key, group in groups.items():
        if len(group) < 2:
            continue
        signatures = {tuple(row[field] for field in signature_fields) for row in group}
        if len(signatures) > 1:
            conflicts.update(row["row_no"] for row in group)
        else:
            ordered = sorted(group, key=lambda row: (row["raw_line"], row["row_no"]))
            identical_drops.update(row["row_no"] for row in ordered[1:])
    return {
        "conflict": conflicts,
        "identical": identical_drops,
        "conflict_code": conflict_code,
        "identical_code": identical_code,
    }


def pass_rate(kept: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(kept * 100.0 / total, 1)
