"""批次与方案版本：历史列表、运行详情、乐观版本校验。

版本号必须严格等于「当前最大版本 + 1」，旧版本提交一律拒绝。
"""
from __future__ import annotations

from ... import errors


def history(store, limit: int = 30) -> list[dict]:
    rows = []
    for run in store.list_runs(limit):
        stats = run["stats"]
        accounts = stats.get("accounts", {})
        latest = store.latest_budget(run["run_id"])
        rows.append({
            "run_id": run["run_id"],
            "dataset": run["dataset"],
            "status": run["status"],
            "created_at": run["created_at"],
            "rule_version": run["rule_version"],
            "inputs": run["manifest"].get("inputs", {}),
            "accounts": accounts,
            "gmv_cents": stats.get("gmv_cents", 0),
            "plan_version": latest["plan_version"] if latest else None,
            "elapsed_ms": run["manifest"].get("elapsed_ms"),
        })
    return rows


def detail(store, run_id: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise errors.NotFoundRun(f"运行不存在：{run_id}")
    budgets = store.budgets(run_id)
    return {
        "run": run,
        "budgets": [{key: value for key, value in budget.items() if key != "payload"}
                    | {"payload": budget["payload"]} for budget in budgets],
        "next_plan_version": store.next_plan_version(run_id),
    }


def check_version(store, run_id: str, plan_version: int) -> int:
    latest = store.next_plan_version(run_id) - 1
    if int(plan_version) <= latest:
        raise errors.StaleVersion(
            f"方案版本 {plan_version} 已过期，当前最大版本 {latest}",
            {"latest_version": latest, "your_version": int(plan_version)})
    if int(plan_version) != latest + 1:
        raise errors.BadRequest(
            f"方案版本必须为 {latest + 1}",
            {"latest_version": latest, "your_version": int(plan_version)})
    return int(plan_version)
