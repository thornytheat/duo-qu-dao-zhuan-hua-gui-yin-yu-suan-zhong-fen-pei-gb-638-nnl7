"""渠道 ROI 与边际 ROI：基础 ROI 来自已归因金额与本渠道花费。

边际 ROI 随加投档数衰减，避免把全部预算压给一个渠道。
"""
from __future__ import annotations

from decimal import Decimal


def base_roi(gmv_cents: int, spend_cents: int) -> Decimal:
    if spend_cents <= 0:
        return Decimal(0)
    return (Decimal(int(gmv_cents)) / Decimal(int(spend_cents))).quantize(Decimal("0.0001"))


def marginal_roi(roi: Decimal, given_steps: int, decay: Decimal) -> Decimal:
    return (Decimal(roi) / (Decimal(1) + Decimal(decay) * int(given_steps))).quantize(
        Decimal("0.0001"))
