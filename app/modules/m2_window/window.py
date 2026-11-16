"""归因窗口：半开区间 [t-window, t)，左闭右开。

朴素实现常把两端都算进，于是恰好在下单时刻的点击被重复计入。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal


def bounds(order_time: datetime, event_type: str, settings) -> tuple[datetime, datetime]:
    span = settings.window_for(event_type)
    return order_time - span, order_time


def contains(start: datetime, end: datetime, moment: datetime) -> bool:
    return start <= moment < end


def delta_days(order_time: datetime, moment: datetime) -> Decimal:
    seconds = Decimal(str((order_time - moment).total_seconds()))
    return seconds / Decimal(86400)


def is_late(ingested_at: datetime | None, order_time: datetime) -> bool:
    """入库时间晚于订单时间：下单后才采到，仍计入但标记迟到。"""
    if ingested_at is None:
        return False
    return ingested_at > order_time


def window_label(event_type: str, settings) -> str:
    span: timedelta = settings.window_for(event_type)
    return f"[t-{span.days}d, t)"
