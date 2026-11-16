"""M8 HTTP 端到端：批次提交、幂等、结转视图、对账、CSV、页面与预算联动。"""
from __future__ import annotations


def _batch(client, batch_id, source="refunds_ok.csv", run_id=None):
    body = {"batch_id": batch_id, "source": source}
    if run_id:
        body["run_id"] = run_id
    return client.post("/api/refunds/batches", json=body)


def test_submit_batch_and_carry_forward(client_dirty):
    response = _batch(client_dirty, "RF-WEB-1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "APPLIED"
    assert payload["applied_rows"] == 4

    view = client_dirty.get("/api/refunds/carry-forward").json()
    assert view["totals"]["gmv_cents"] - view["totals"]["refund_cents"] \
        == view["totals"]["net_cents"]
    assert view["reconcile"]["all_balanced"] is True


def test_retry_batch_is_idempotent_over_http(client_dirty):
    assert _batch(client_dirty, "RF-WEB-1").status_code == 200
    again = _batch(client_dirty, "RF-WEB-1")
    assert again.status_code == 200
    assert again.json()["idempotent"] is True
    listing = client_dirty.get("/api/refunds/batches").json()
    assert listing["count"] == 1


def test_conflicting_batch_returns_409(client_dirty):
    _batch(client_dirty, "RF-WEB-1")
    conflict = client_dirty.post("/api/refunds/batches", json={
        "batch_id": "RF-WEB-1",
        "content": ("refund_id,order_id,refund,refund_time,unit\n"
                    "X,O-1001,1.00,2026-11-12T10:00:00+08:00,元\n"),
    })
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "E_REFUND_CONFLICT"


def test_mixed_batch_partial_and_unknown_run_404(client_dirty):
    mixed = _batch(client_dirty, "RF-WEB-2", "refunds_mixed.csv")
    assert mixed.status_code == 200
    assert mixed.json()["status"] == "PARTIAL"
    detail = client_dirty.get("/api/refunds/batches/RF-WEB-2").json()
    assert len(detail["rows"]) == 8

    missing = client_dirty.get(
        "/api/refunds/carry-forward", params={"run_id": "run-nope"})
    assert missing.status_code == 404


def test_reconcile_and_allocations_csv(client_dirty):
    _batch(client_dirty, "RF-WEB-1")
    rec = client_dirty.get("/api/refunds/reconcile").json()["reconcile"]
    assert rec["refund_balanced"] is True
    assert rec["net_balanced"] is True
    csv_response = client_dirty.get(
        "/api/refunds/allocations.csv", params={"model": "linear"})
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    assert "冲减(分)" in csv_response.text


def test_refund_page_renders(client_dirty):
    _batch(client_dirty, "RF-WEB-1")
    page = client_dirty.get("/refunds")
    assert page.status_code == 200
    assert 'data-page="refunds"' in page.text
    assert "四账对平" in page.text


def test_refund_page_without_run_renders(client):
    page = client.get("/refunds")
    assert page.status_code == 200
    assert 'data-page="refunds"' in page.text


def test_budget_uses_refund_net_after_carry_forward(client_dirty):
    _batch(client_dirty, "RF-WEB-1")
    original = client_dirty.post(
        "/api/budgets", json={"plan_version": 1, "total_yuan": 60000})
    assert original.status_code == 200
    net_plan = client_dirty.post("/api/budgets", json={
        "plan_version": 2, "total_yuan": 60000, "use_refund_net": True})
    assert net_plan.status_code == 200
    payload = net_plan.json()
    assert payload["roi_basis"] == "refund_net"
    assert payload["refund_net_cents"]["refund_cents"] > 0
    # 旧版本仍为原口径
    plans = client_dirty.get("/api/budgets").json()["plans"]
    assert plans[1]["payload"]["roi_basis"] == "original_gmv"


def test_resume_endpoint_recovers_running_batch(client_dirty, store):
    run = store.latest_run()
    _batch(client_dirty, "RF-WEB-1")
    # 直接用同内容打续跑入口：已终态 → 幂等回放
    resumed = client_dirty.post(
        "/api/refunds/batches/RF-WEB-1/resume", json={"source": "refunds_ok.csv"})
    assert resumed.status_code == 200
    assert resumed.json()["idempotent"] is True
    assert resumed.json()["run_id"] == run["run_id"]
