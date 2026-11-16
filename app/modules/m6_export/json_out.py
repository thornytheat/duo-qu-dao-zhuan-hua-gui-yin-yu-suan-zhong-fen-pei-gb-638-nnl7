"""JSON 导出：金额给整数分，编号保持字符串，时间给 UTC ISO。

超过 2^53 的编号不会经过 int()，导出值与输入逐字相同。
"""
from __future__ import annotations

from ..m4_attribution import models as m4_models


def _safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return str(value)


def bundle(run: dict, accounts: dict, models_summary: dict, spend: dict,
           touches: list[dict], orders: list[dict], credits: list[dict],
           refunds: dict | None = None) -> dict:
    return _safe({
        "run_id": run["run_id"],
        "dataset": run["dataset"],
        "rule_version": run["rule_version"],
        "created_at": run["created_at"],
        "status": run["status"],
        "manifest": run["manifest"],
        "accounts": accounts,
        "money": {"unit": "分", "note": "整数分，除 100 得元"},
        "models": [{"code": code, "label": m4_models.label(code), "gmv_cents": value}
                   for code, value in models_summary.items()],
        "spend": spend,
        "refunds": refunds,
        "touchpoints": touches,
        "orders": orders,
        "credits": credits,
    })
