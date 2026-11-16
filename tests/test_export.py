"""M6 报告导出：证据列、份额合计、整数分、清单复算。"""
from __future__ import annotations

from app.modules.m6_export import json_out, manifest_out, service


def test_touch_csv_has_evidence_columns(client_dirty):
    body = client_dirty.get("/api/export.csv?kind=touch").text
    assert "物理行" in body and "原因码" in body
    assert "TP-0016" in body
    assert body.count("\n") == 29


def test_credit_csv_sums_to_model_total(client_dirty, store):
    body = client_dirty.get("/api/export.csv?kind=credit&model=time_decay").text
    lines = [line for line in body.strip().splitlines() if line]
    total = sum(int(line.split(",")[5]) for line in lines[1:])
    run = store.latest_run()
    assert total == store.model_totals(run["run_id"])["time_decay"]["gmv_cents"]


def test_export_json_uses_integer_cents(client_dirty):
    payload = client_dirty.get("/api/export.json").json()
    assert payload["money"]["unit"] == "分"
    assert len(payload["touchpoints"]) == 28
    totals = {item["code"]: item["gmv_cents"] for item in payload["models"]}
    credit_total = sum(row["gmv_cents"] for row in payload["credits"])
    assert credit_total == totals["time_decay"]


def test_manifest_recomputes_run_id(client_dirty):
    manifest = client_dirty.get("/api/manifest").json()
    assert manifest["replayable"] is True
    assert manifest["recomputed_run_id"] == manifest["run_id"]
    assert len(manifest["inputs"]["touchpoints.csv"]["sha256"]) == 64
    assert manifest["inputs"]["budget_plan.json"]["bytes"] > 0


def test_big_order_id_stays_string():
    order_id = "9007199254740993"   # 超过 2^53，不能过 int()
    run = {"run_id": "run-x", "dataset": "dirty", "rule_version": "v1",
           "created_at": "2026-11-15T00:00:00Z", "status": "done", "manifest": {}}
    bundle = json_out.bundle(run, {}, {}, {}, [], [{"order_id": order_id, "gmv_cents": 1}], [])
    assert bundle["orders"][0]["order_id"] == order_id


def test_unknown_export_kind_rejected(client_dirty):
    response = client_dirty.get("/api/export.csv?kind=bogus")
    assert response.status_code == 400
    assert "touch" in response.json()["allowed"]


def test_order_csv_matches_order_rows(client_dirty):
    body = client_dirty.get("/api/export.csv?kind=order").text
    assert body.count("\n") == 9
    assert "O-1007" in body


def test_manifest_export_is_replayable(settings, store, run_id):
    manifest = manifest_out.build(store.get_run(run_id), settings)
    exported = service.manifest(store, run_id, settings)
    assert manifest["replayable"] is True
    assert exported["run_id"] == run_id
