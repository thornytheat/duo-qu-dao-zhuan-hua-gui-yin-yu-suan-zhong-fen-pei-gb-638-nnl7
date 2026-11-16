"""退款按原订单的触点份额拆分：只引用既有归因证据，不重算历史。

一笔退款对应一张原订单：该订单在某个模型下的 credits 行就是原始份额，
退款额沿用同一套触点/渠道占比，用整数分 + 最大余额法拆分，拆完逐单守恒。
历史 credits 一行不改，退款份额单独落到 refund_allocations。
"""
from __future__ import annotations

from decimal import Decimal

from ..m4_attribution import allocate


def order_credit_share(credits: list[dict]) -> list[dict]:
    """取一张订单在某模型下的份额行，按位次稳定排序。"""
    rows = [row for row in credits]
    rows.sort(key=lambda item: (int(item["position"]), str(item["touch_id"])))
    return rows


def split_refund(refund_cents: int, order_credits: list[dict]) -> list[dict]:
    """把退款额按原单触点份额拆成渠道行。

    权重直接取各触点的原始份额（整数分），零份额触点权重为 0；
    原单全部份额为 0（理论上不会出现）时退化为触点均摊，仍保证守恒。
    返回每条触点一行，金额合计严格等于退款额。
    """
    refund_cents = int(refund_cents)
    if refund_cents <= 0:
        return []
    rows = order_credit_share(order_credits)
    if not rows:
        return []
    weights = [Decimal(max(int(row["gmv_cents"] or 0), 0)) for row in rows]
    if sum(weights) <= 0:
        weights = [Decimal(1)] * len(rows)
    parts = allocate.split_cents(refund_cents, weights)
    result = []
    for row, cents in zip(rows, parts):
        if cents <= 0:
            continue
        result.append({
            "order_id": row["order_id"],
            "touch_id": row["touch_id"],
            "channel": row["channel"],
            "position": int(row["position"]),
            "cents": int(cents),
        })
    return result
