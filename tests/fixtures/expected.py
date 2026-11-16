"""样例数据的期望值。

这些数字由规则推导（不是从运行结果抄回来的）：
dirty 触点 28 行 = 计入 14 + 迟到 1 + 不计入 13，其中窗口外 4、隔离 9。
"""
from __future__ import annotations

DIRTY = {
    "touch_input": 28,
    "touch_counted": 14,
    "touch_late": 1,
    "touch_dropped": 13,
    "touch_out_of_window": 4,
    "touch_quarantined": 9,
    "order_input": 8,
    "order_ok": 5,
    "order_quarantined": 3,
    "alias_quarantined": 1,
    "quarantine_total": 13,
    "gmv_cents": 1339850,
}

REASON_COUNTS = {
    "OD_DUP_CONFLICT": 2,
    "TD_CHANNEL_UNKNOWN": 2,
    "TD_COL_COUNT": 2,
    "TD_TOUCH_DUP_CONFLICT": 2,
    "ALIAS_BAD_ROW": 1,
    "OD_GMV_INVALID": 1,
    "TD_COST_INVALID": 1,
    "TD_EVENT_TYPE_UNKNOWN": 1,
    "TD_TIME_UNPARSABLE": 1,
}

CLEAN = {"touch_counted": 6, "gmv_cents": 285900, "quarantine": 0}


def make_touch(row_no: int, touch_id: str, event_iso: str, channel: str = "douyin_feed",
               event_type: str = "click", late: bool = False, cost_cents: int = 10000) -> dict:
    return {
        "row_no": row_no,
        "touch_id": touch_id,
        "channel": channel,
        "event_type": event_type,
        "event_time_utc": event_iso,
        "late": late,
        "cost_cents": cost_cents,
        "status": "COUNTED",
        "reason": None,
    }


def make_order(order_id: str, gmv_cents: int, order_iso: str) -> dict:
    return {
        "order_id": order_id,
        "gmv_cents": gmv_cents,
        "order_time_utc": order_iso,
        "row_no": 1,
        "status": "OK",
        "reason": None,
    }
