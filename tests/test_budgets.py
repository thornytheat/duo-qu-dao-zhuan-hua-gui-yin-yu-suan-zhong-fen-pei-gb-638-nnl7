"""M5 预算重分配：边际 ROI 贪心、上下限约束、版本锁。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m5_budgets import greedy
from app.modules.m5_budgets import roi as roi_rules

CONSTRAINTS = {code: {"min_cents": 100000, "cap_cents": 2000000}
               for code in ("a", "b", "c")}
ROIS = {"a": Decimal("3.0"), "b": Decimal("2.0"), "c": Decimal("1.0")}


def _plan(budget=3000000, constraints=None, step=100000, decay="0.15"):
    return greedy.allocate(budget, constraints or CONSTRAINTS, ROIS, step, Decimal(decay))


def test_allocation_conserves_total():
    result = _plan()
    # TRAP-24：分完仍等于总额，差额为 0
    assert sum(result["spend"].values()) == 3000000
    assert result["balanced"] is True


def test_caps_and_minimums_respected():
    result = _plan()
    for code, value in result["spend"].items():
        assert CONSTRAINTS[code]["min_cents"] <= value <= CONSTRAINTS[code]["cap_cents"]


def test_greedy_is_deterministic_under_input_order():
    shuffled = {"c": CONSTRAINTS["c"], "a": CONSTRAINTS["a"], "b": CONSTRAINTS["b"]}
    first = _plan()["spend"]
    second = _plan(constraints=shuffled)["spend"]
    # TRAP-19：同分时按渠道编号升序，换输入顺序结果不变
    assert first == second
    assert first["a"] >= first["b"] >= first["c"]


def test_marginal_roi_decays_with_steps():
    assert roi_rules.marginal_roi(Decimal("2.0"), 0, Decimal("0.15")) == Decimal("2.0000")
    assert roi_rules.marginal_roi(Decimal("2.0"), 4, Decimal("0.15")) < Decimal("2.0")
    assert roi_rules.base_roi(2000, 0) == Decimal(0)
    assert roi_rules.base_roi(2000, 1000) == Decimal("2.0000")


def test_stale_plan_version_rejected(client_dirty):
    first = client_dirty.post("/api/budgets", json={"plan_version": 1, "total_yuan": 60000})
    assert first.status_code == 200
    # TRAP-26：旧版本提交一律拒绝，不覆盖新数据
    stale = client_dirty.post("/api/budgets", json={"plan_version": 1, "total_yuan": 50000})
    assert stale.status_code == 409
    assert stale.json()["latest_version"] == 1
    assert stale.json()["error"] == "E_STALE_PLAN_VERSION"


def test_budget_below_minimum_rejected(client_dirty):
    response = client_dirty.post("/api/budgets", json={"plan_version": 1, "total_yuan": 10000})
    assert response.status_code == 422
    assert response.json()["error"] == "E_BUDGET_INFEASIBLE"


def test_budget_above_cap_total_rejected(client_dirty):
    response = client_dirty.post("/api/budgets", json={"plan_version": 1, "total_yuan": 90000})
    assert response.status_code == 422


def test_plan_versions_are_monotonic(client_dirty):
    assert client_dirty.post("/api/budgets",
                             json={"plan_version": 1, "total_yuan": 60000}).status_code == 200
    assert client_dirty.post("/api/budgets",
                             json={"plan_version": 2, "total_yuan": 62000}).status_code == 200
    payload = client_dirty.get("/api/budgets").json()
    assert payload["next_plan_version"] == 3
    assert [item["plan_version"] for item in payload["plans"]] == [2, 1]
    rows = payload["plans"][0]["payload"]["rows"]
    assert sum(row["spend_new_cents"] for row in rows) == 6200000
