"""金额拆分：整数分 + 最大余额法，拆完必须等于原额。

朴素实现各自四舍五入，合计会差几分；这里先把小数部分排序补足，守恒可断言。
"""
from __future__ import annotations

from decimal import Decimal


def split_cents(total_cents: int, weights: list[Decimal]) -> list[int]:
    count = len(weights)
    if count == 0:
        return []
    total = int(total_cents)
    if total <= 0:
        return [0] * count
    total_weight = sum(weights)
    if total_weight <= 0:
        parts = [0] * count
        parts[0] = total
        return parts

    exact = [Decimal(total) * Decimal(weight) / total_weight for weight in weights]
    parts = [int(value) for value in exact]
    remainder = total - sum(parts)
    ranking = sorted(range(count), key=lambda index: (-(exact[index] - parts[index]), index))
    for index in ranking[:remainder]:
        parts[index] += 1
    return parts
