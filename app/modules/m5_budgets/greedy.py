"""预算分配：按边际 ROI 贪心加档，受渠道上下限约束，分完等于总额。

同分时按渠道编号升序，结果与渠道输入顺序无关。
"""
from __future__ import annotations

from decimal import Decimal

from ... import errors
from . import roi as roi_rules


def allocate(budget_cents: int, constraints: dict, rois: dict,
             step_cents: int, decay: Decimal) -> dict:
    channels = sorted(constraints)
    minimums = {code: int(constraints[code]["min_cents"]) for code in channels}
    caps = {code: int(constraints[code]["cap_cents"]) for code in channels}
    budget = int(budget_cents)
    if budget < sum(minimums.values()):
        raise errors.BudgetInfeasible(
            "预算低于各渠道下限合计",
            {"budget_cents": budget, "min_total_cents": sum(minimums.values())})
    if budget > sum(caps.values()):
        raise errors.BudgetInfeasible(
            "预算高于各渠道上限合计",
            {"budget_cents": budget, "cap_total_cents": sum(caps.values())})

    spend = dict(minimums)
    steps = {code: 0 for code in channels}
    remaining = budget - sum(minimums.values())
    trace: list[dict] = []

    while remaining >= step_cents:
        best = None
        for code in channels:
            if spend[code] + step_cents > caps[code]:
                continue
            value = roi_rules.marginal_roi(rois[code], steps[code], decay)
            if best is None or value > best[1]:
                best = (code, value)
        if best is None:
            break
        code, value = best
        spend[code] += step_cents
        steps[code] += 1
        remaining -= step_cents
        trace.append({"channel": code, "marginal_roi": float(value),
                      "added_cents": step_cents})

    while remaining > 0:
        candidates = [(roi_rules.marginal_roi(rois[code], steps[code], decay), code)
                      for code in channels if spend[code] < caps[code]]
        if not candidates:
            raise errors.BudgetInfeasible("渠道上限不足以容纳剩余金额")
        candidates.sort(key=lambda item: (-item[0], item[1]))
        value, code = candidates[0]
        take = min(remaining, caps[code] - spend[code])
        spend[code] += take
        remaining -= take
        trace.append({"channel": code, "marginal_roi": float(value),
                      "added_cents": take})

    allocated = sum(spend.values())
    if allocated != budget:
        raise errors.BudgetInfeasible(
            "分配结果与预算不等", {"allocated_cents": allocated, "budget_cents": budget})

    return {
        "spend": spend,
        "steps": steps,
        "trace": trace,
        "minimums": minimums,
        "caps": caps,
        "budget_cents": budget,
        "balanced": allocated == budget,
    }
