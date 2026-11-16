"""权重规则：时间衰减与位置加权。

衰减权重用 2^(-delta/half_life) 计算，精度 1e-12；金额分配在 allocate 里用整数分完成。
"""
from __future__ import annotations

import math
from decimal import Decimal

PRECISION = Decimal("1e-12")


def time_decay(deltas_days: list[Decimal], half_life_days: Decimal) -> list[Decimal]:
    if half_life_days <= 0:
        return [Decimal(1) for _ in deltas_days]
    weights = []
    for delta in deltas_days:
        exponent = float(Decimal(delta) / Decimal(half_life_days))
        weights.append(Decimal(str(math.pow(0.5, exponent))).quantize(PRECISION))
    return weights


def position(count: int) -> list[Decimal]:
    if count <= 0:
        return []
    if count == 1:
        return [Decimal(1)]
    if count == 2:
        return [Decimal("0.5"), Decimal("0.5")]
    middle = Decimal("0.2") / Decimal(count - 2)
    return [Decimal("0.4")] + [middle] * (count - 2) + [Decimal("0.4")]
