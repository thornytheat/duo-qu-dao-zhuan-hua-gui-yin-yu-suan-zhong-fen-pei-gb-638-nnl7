"""端到端：导入 → 归因 → 预算 → 导出 → 六个页面。"""
from __future__ import annotations

from fixtures.expected import DIRTY

PAGES = (
    ("/", 'data-page="overview"'),
    ("/touchpoints", 'data-page="touchpoints"'),
    ("/quarantine", 'data-page="quarantine"'),
    ("/attribution", 'data-page="attribution"'),
    ("/confidence", 'data-page="confidence"'),
    ("/budgets", 'data-page="budgets"'),
    ("/refunds", 'data-page="refunds"'),
    ("/export", 'data-page="export"'),
)


def test_full_pipeline_from_sample_data(client):
    response = client.post("/api/import", json={"dataset": "dirty"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["repeat"] is False
    assert body["accounts"]["touch_input"] == DIRTY["touch_input"]
    assert body["accounts"]["touch_counted"] == DIRTY["touch_counted"]
    assert body["accounts"]["touch_late"] == DIRTY["touch_late"]
    assert body["reconcile"]["gmv_balanced"] is True


def test_all_pages_render(client_dirty):
    for path, marker in PAGES:
        response = client_dirty.get(path)
        assert response.status_code == 200
        assert marker in response.text


def test_rerun_is_idempotent(client_dirty, store):
    first = store.latest_run()
    again = client_dirty.post("/api/import", json={"dataset": "dirty"}).json()
    # 不变量：同输入重跑命中同一个批次键，行数不变、不新增记录
    assert again["run_id"] == first["run_id"]
    assert again["repeat"] is True
    assert store.count_runs() == 1
    assert len(store.touchpoints(again["run_id"])) == DIRTY["touch_input"]
    assert store.latest_run()["stats"]["accounts"] == first["stats"]["accounts"]


def test_budget_to_export_flow(client_dirty):
    plan = client_dirty.post("/api/budgets", json={"plan_version": 1, "total_yuan": 60000})
    assert plan.status_code == 200
    payload = plan.json()
    assert payload["balanced"] is True
    assert sum(row["spend_new_cents"] for row in payload["rows"]) == 6000000
    assert payload["rows"][0]["marginal_roi"]
    export = client_dirty.get("/api/export.json").json()
    assert export["status"] == "done"
    manifest = client_dirty.get("/api/manifest").json()
    assert manifest["replayable"] is True


def test_unknown_run_returns_404(client_dirty):
    response = client_dirty.get("/api/runs/run-unknown")
    assert response.status_code == 404
    assert response.json()["error"] == "E_RUN_NOT_FOUND"
