"""五个归因模型的权重入口：首次、末次、线性、时间衰减、位置加权。"""
from __future__ import annotations

from decimal import Decimal

from . import decay

MODELS = (
    ("first", "首次触点"),
    ("last", "末次触点"),
    ("linear", "线性均分"),
    ("time_decay", "时间衰减"),
    ("position", "位置加权"),
)
MODEL_CODES = tuple(code for code, _ in MODELS)
MODEL_LABELS = dict(MODELS)


def label(code: str) -> str:
    return MODEL_LABELS.get(code, code)


def weights(model: str, deltas_days: list[Decimal], half_life_days: Decimal) -> list[Decimal]:
    count = len(deltas_days)
    if count == 0:
        return []
    if model == "first":
        return [Decimal(1)] + [Decimal(0)] * (count - 1)
    if model == "last":
        return [Decimal(0)] * (count - 1) + [Decimal(1)]
    if model == "linear":
        return [Decimal(1)] * count
    if model == "time_decay":
        return decay.time_decay(deltas_days, half_life_days)
    if model == "position":
        return decay.position(count)
    raise ValueError(f"未知归因模型：{model}")
