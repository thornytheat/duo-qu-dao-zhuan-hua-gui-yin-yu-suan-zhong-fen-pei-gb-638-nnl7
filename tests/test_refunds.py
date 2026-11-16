"""M8 退款结转：按原单份额冲减、幂等、断点续跑、失败留旧版、四账对平。"""
from __future__ import annotations

import pytest

from app import errors
from app.modules.m4_attribution import models as m4_models
from app.modules.m5_budgets import service as m5_service
from app.modules.m8_refunds import allocate, parse, service

OK_CSV = "refunds_ok.csv"
MIXED_CSV = "refunds_mixed.csv"


def _process(settings, store, run_id, batch_id, source=OK_CSV):
    return service.process_batch(settings, store, run_id, batch_id, source)


# ---------- 纯函数：按原单份额拆分，逐单守恒 ----------

def test_split_refund_follows_original_credit_share():
    credits = [
        {"order_id": "O1", "touch_id": "T1", "channel": "a", "gmv_cents": 700, "position": 0},
        {"order_id": "O1", "touch_id": "T2", "channel": "b", "gmv_cents": 300, "position": 1},
    ]
    parts = allocate.split_refund(200, credits)
    assert sum(p["cents"] for p in parts) == 200
    assert parts[0]["channel"] == "a" and parts[0]["cents"] == 140
    assert parts[1]["channel"] == "b" and parts[1]["cents"] == 60


def test_split_refund_conserves_odd_amount_with_largest_remainder():
    credits = [
        {"order_id": "O1", "touch_id": "T1", "channel": "a", "gmv_cents": 1, "position": 0},
        {"order_id": "O1", "touch_id": "T2", "channel": "b", "gmv_cents": 1, "position": 1},
        {"order_id": "O1", "touch_id": "T3", "channel": "c", "gmv_cents": 1, "position": 2},
    ]
    parts = allocate.split_refund(100, credits)
    assert sorted(p["cents"] for p in parts) == [33, 33, 34]
    assert sum(p["cents"] for p in parts) == 100


def test_split_refund_zero_and_negative_guarded():
    assert allocate.split_refund(0, [{"gmv_cents": 1}]) == []
    assert allocate.split_refund(-5, [{"gmv_cents": 1}]) == []


# ---------- 解析与归一 ----------

def test_parse_normalizes_amount_and_time_to_cents_utc():
    text = ("refund_id,order_id,refund,refund_time,unit\n"
            "R1,O-1001,299.00,2026-11-12T10:00:00+08:00,元\n")
    rows = parse.normalize(parse.parse_text(text))["rows"]
    assert rows[0]["refund_cents"] == 29900
    assert rows[0]["refund_time_utc"].endswith("Z")
    assert rows[0]["status"] == "PENDING"


def test_parse_quarantines_bad_amount_and_time():
    text = ("refund_id,order_id,refund,refund_time,unit\n"
            "R1,O-1001,N/A,2026-11-12T10:00:00+08:00,元\n"
            "R2,O-1001,10.00,bad-time,元\n")
    rows = parse.normalize(parse.parse_text(text))["rows"]
    assert rows[0]["reason"] == "RF_AMOUNT_INVALID"
    assert rows[1]["reason"] == "RF_TIME_UNPARSABLE"
    assert all(r["status"] == "QUARANTINED" for r in rows)


# ---------- 批次处理：成功、隔离、幂等、冲突 ----------

def test_batch_applied_and_carry_forward_balanced(settings, store, run_id):
    result = _process(settings, store, run_id, "RF-OK")
    assert result["status"] == "APPLIED"
    assert result["applied_rows"] == 4
    view = service.carry_forward(store, run_id, "time_decay")
    assert view["totals"]["gmv_cents"] - view["totals"]["refund_cents"] \
        == view["totals"]["net_cents"]
    assert view["reconcile"]["all_balanced"] is True


def test_refund_does_not_rewrite_history(settings, store, run_id):
    orders_before = [dict(o) for o in store.orders(run_id)]
    credits_before = [dict(c) for c in store.credits(run_id, "time_decay")]
    _process(settings, store, run_id, "RF-OK")
    assert store.orders(run_id) == orders_before
    assert store.credits(run_id, "time_decay") == credits_before


def test_retry_same_batch_is_idempotent(settings, store, run_id):
    first = _process(settings, store, run_id, "RF-OK")
    second = _process(settings, store, run_id, "RF-OK")
    assert second["idempotent"] is True
    assert second["refund_cents"] == first["refund_cents"]
    # 份额行没有翻倍
    alloc = store.refund_allocations(run_id, "time_decay")
    assert sum(a["cents"] for a in alloc) == first["refund_cents"]
    view = service.carry_forward(store, run_id, "time_decay")
    assert view["reconcile"]["refund_balanced"] is True


def test_same_batch_id_with_different_content_conflicts(settings, store, run_id):
    _process(settings, store, run_id, "RF-OK")
    content = ("refund_id,order_id,refund,refund_time,unit\n"
               "RF-X,O-1001,10.00,2026-11-12T10:00:00+08:00,元\n")
    with pytest.raises(errors.RefundConflict) as exc:
        service.process_batch(settings, store, run_id, "RF-OK", content=content)
    assert exc.value.status == 409
    # 旧批次原样保留
    assert store.get_refund_batch(run_id, "RF-OK")["status"] == "APPLIED"


def test_mixed_batch_quarantines_invalid_rows(settings, store, run_id):
    result = _process(settings, store, run_id, "RF-MIX", MIXED_CSV)
    assert result["status"] == "PARTIAL"
    reasons = {r["refund_id"]: r["reason"] for r in result["rows"] if r["reason"]}
    assert "RF-B-002" in reasons and reasons["RF-B-002"] == "RF_OVER_REFUND"
    assert reasons["RF-B-003"] == "RF_ORDER_UNKNOWN"
    assert reasons["RF-B-004"] == "RF_ORDER_INVALID"
    assert reasons["RF-B-005"] == "RF_AMOUNT_INVALID"
    # 同批次重复行只冲减一次
    applied = [r for r in result["rows"] if r["status"] == "APPLIED"
               and r["refund_id"] == "RF-B-006"]
    assert len(applied) == 1
    assert service.carry_forward(store, run_id, "time_decay")["reconcile"][
        "all_balanced"] is True


def test_over_refund_checked_across_batches(settings, store, run_id):
    # 第一批把 O-1001 退掉 299.00；第二批再退超过剩余额度应隔离
    _process(settings, store, run_id, "RF-1", OK_CSV)
    content = ("refund_id,order_id,refund,refund_time,unit\n"
               "R9,O-1001,1100.00,2026-11-20T10:00:00+08:00,元\n")
    result = service.process_batch(settings, store, run_id, "RF-2", content=content)
    assert result["rows"][0]["reason"] == "RF_OVER_REFUND"
    assert result["applied_rows"] == 0


# ---------- 断点续跑 ----------

def test_resume_from_checkpoint(settings, store, run_id):
    from app.modules.m8_refunds import parse as p
    text = (settings.sample_dir / "refunds" / OK_CSV).read_text(encoding="utf-8")
    rows = p.normalize(p.parse_text(text))["rows"]
    digest = p.batch_digest(rows)
    store.save_refund_batch(run_id, {
        "batch_id": "RF-HALF", "status": "RUNNING", "source": OK_CSV,
        "total_rows": len(rows), "processed_rows": 2, "applied_rows": 2,
        "quarantined_rows": 0,
        "refund_cents": sum(r["refund_cents"] for r in rows[:2]),
        "payload": {}, "created_at": "2026-11-14T00:00:00Z",
        "updated_at": "2026-11-14T00:00:00Z", "error": None, "content_digest": digest})
    for row in rows[:2]:
        row["status"] = "APPLIED"
        row["applied"] = True
        store.upsert_refund_row(run_id, "RF-HALF", row)

    resumed = _process(settings, store, run_id, "RF-HALF")
    assert resumed["status"] == "APPLIED"
    assert resumed["resumed"] is True
    assert resumed["applied_rows"] == 4
    # 再跑不重复冲减
    again = _process(settings, store, run_id, "RF-HALF")
    assert again["idempotent"] is True
    view = service.carry_forward(store, run_id, "time_decay")
    assert view["reconcile"]["all_balanced"] is True


def test_resume_replays_checkpoint_from_reparsed_file(settings, store, run_id,
                                                      monkeypatch):
    """真实中断：处理到第 3 行写检查点时抛错，重提同一文件从断点继续。"""
    parsed = parse.normalize(parse.parse_text(
        (settings.sample_dir / "refunds" / OK_CSV).read_text(encoding="utf-8")))
    digest = parse.batch_digest(parsed["rows"])
    real_upsert = store.upsert_refund_row
    calls = {"n": 0}

    def stop_on_third(run_id, batch_id, row):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("模拟进程中断")
        real_upsert(run_id, batch_id, row)

    monkeypatch.setattr(store, "upsert_refund_row", stop_on_third)
    with pytest.raises(errors.RefundInfeasible):
        service.process_batch(settings, store, run_id, "RF-CRASH", OK_CSV)
    # 中断留下逐行检查点：前两行已 APPLIED，批次标 FAILED
    assert store.get_refund_batch(run_id, "RF-CRASH")["status"] == "FAILED"
    saved = store.refund_rows(run_id, "RF-CRASH")
    assert sum(1 for r in saved if r["status"] == "APPLIED") == 2

    # 同内容重试：行状态从库回灌，前两行不重复冲减
    monkeypatch.setattr(store, "upsert_refund_row", real_upsert)
    result = service.process_batch(settings, store, run_id, "RF-CRASH", OK_CSV)
    assert result["status"] == "APPLIED"
    assert result["applied_rows"] == 4
    view = service.carry_forward(store, run_id, "time_decay")
    # 退款额只计一次，没有因重试翻倍
    assert view["totals"]["refund_cents"] == result["refund_cents"]
    assert view["reconcile"]["all_balanced"] is True
    assert digest  # 指纹参与整条链路


# ---------- 失败保留旧版 ----------

def test_failure_keeps_old_budgets_and_marks_batch_failed(settings, store, run_id,
                                                          monkeypatch):
    original_batch = _process(settings, store, run_id, "RF-OK")
    # 先有一个已审批预算版本
    m5_service.reallocate(settings, store, run_id, 1, 6_000_000)
    budgets_before = len(store.budgets(run_id))

    def boom(*args, **kwargs):
        raise RuntimeError("磁盘中断")

    monkeypatch.setattr(service, "_allocate_row", boom)
    with pytest.raises(errors.RefundInfeasible):
        service.process_batch(settings, store, run_id, "RF-BOOM",
                              content=("refund_id,order_id,refund,refund_time,unit\n"
                                       "R1,O-1001,50.00,2026-11-20T10:00:00+08:00,元\n"))
    failed = store.get_refund_batch(run_id, "RF-BOOM")
    assert failed["status"] == "FAILED"
    assert failed["error"]
    # 旧批次与已审批预算原样保留，失败批次无份额残留
    kept = store.get_refund_batch(run_id, "RF-OK")
    assert kept["status"] == original_batch["status"]
    assert kept["refund_cents"] == original_batch["refund_cents"]
    assert len(store.budgets(run_id)) == budgets_before
    assert store.refund_allocations(run_id, "time_decay", "RF-BOOM") == []


def test_failed_batch_can_retry_with_same_content(settings, store, run_id, monkeypatch):
    content = ("refund_id,order_id,refund,refund_time,unit\n"
               "R1,O-1001,50.00,2026-11-20T10:00:00+08:00,元\n")
    calls = {"n": 0}
    real = service._allocate_row

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("瞬时故障")
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "_allocate_row", flaky)
    with pytest.raises(errors.RefundInfeasible):
        service.process_batch(settings, store, run_id, "RF-RETRY", content=content)
    monkeypatch.setattr(service, "_allocate_row", real)
    result = service.process_batch(settings, store, run_id, "RF-RETRY", content=content)
    assert result["status"] == "APPLIED"
    assert result["applied_rows"] == 1


# ---------- 净额 ROI 只影响下一版预算 ----------

def test_budget_with_refund_net_keeps_old_version_intact(settings, store, run_id):
    _process(settings, store, run_id, "RF-OK")
    v1 = m5_service.reallocate(settings, store, run_id, 1, 6_000_000)
    assert v1["roi_basis"] == "original_gmv"
    v2 = m5_service.reallocate(settings, store, run_id, 2, 6_000_000,
                               use_refund_net=True)
    assert v2["roi_basis"] == "refund_net"
    assert v2["refund_net_cents"]["refund_cents"] > 0
    # 旧版本 payload 不被改写
    assert store.budgets(run_id)[1]["payload"]["roi_basis"] == "original_gmv"
    # 两版各自守恒
    for plan in (v1, v2):
        assert sum(r["spend_new_cents"] for r in plan["rows"]) == 6_000_000


def test_each_model_balances_and_covers_all_channels(settings, store, run_id):
    _process(settings, store, run_id, "RF-OK")
    for model in m4_models.MODEL_CODES:
        view = service.carry_forward(store, run_id, model)
        assert view["reconcile"]["refund_balanced"] is True
        assert view["reconcile"]["net_balanced"] is True
        # 每个模型的退款拆分合计都等于退款批次行金额
        assert view["totals"]["refund_cents"] == view["reconcile"]["refund_row_cents"]
