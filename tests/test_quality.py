"""M3 质量隔离：原因码优先级、重复键冲突、迟到标记、四账。"""
from __future__ import annotations

from app import reasons
from app.modules.m3_quality import rules

from fixtures.expected import DIRTY, REASON_COUNTS


def test_reason_priority_is_independent_of_rule_order():
    row = {"row_no": 1, "raw_line": 2, "touch_id": "T1", "channel": None,
           "event_type": None, "errors": []}
    codes = rules.touch_codes(row, None)
    assert set(codes) == {"TD_CHANNEL_UNKNOWN", "TD_EVENT_TYPE_UNKNOWN"}
    # TRAP-19：一行同时命中两条规则，取固定优先级，换顺序结果不变
    assert reasons.pick(codes) == "TD_CHANNEL_UNKNOWN"
    assert reasons.pick(list(reversed(codes))) == "TD_CHANNEL_UNKNOWN"
    assert reasons.PRIORITY["TD_CHANNEL_UNKNOWN"] < reasons.PRIORITY["TD_EVENT_TYPE_UNKNOWN"]


def test_duplicate_touch_id_conflict_quarantines_both(store, run_id):
    rows = [row for row in store.touchpoints(run_id) if row["touch_id"] == "TP-0016"]
    # TRAP-14：同一编号但渠道与成本不同，两条都隔离，不按后写覆盖
    assert len(rows) == 2
    assert {row["reason"] for row in rows} == {"TD_TOUCH_DUP_CONFLICT"}
    assert {row["status"] for row in rows} == {"DROPPED"}


def test_identical_duplicate_keeps_first():
    rows = [
        {"row_no": 1, "raw_line": 2, "touch_id": "T1", "event_time_utc": "2026-11-05T10:00:00Z",
         "channel": "douyin_feed", "cost_cents": 100, "event_type": "click"},
        {"row_no": 2, "raw_line": 3, "touch_id": "T1", "event_time_utc": "2026-11-05T10:00:00Z",
         "channel": "douyin_feed", "cost_cents": 100, "event_type": "click"},
    ]
    flags = rules.duplicate_flags("touch", rows)
    assert flags["conflict"] == set()
    assert flags["identical"] == {2}


def test_identical_duplicate_order_keeps_first_row():
    rows = [
        {"row_no": 1, "raw_line": 2, "order_id": "O-9", "gmv_cents": 10000,
         "order_time_utc": "2026-11-05T13:00:00Z"},
        {"row_no": 2, "raw_line": 3, "order_id": "O-9", "gmv_cents": 10000,
         "order_time_utc": "2026-11-05T13:00:00Z"},
    ]
    flags = rules.duplicate_flags("order", rows)
    # TRAP-28 上游：内容相同的重复订单只留首条，另一条隔离，不两行都算成交
    assert flags["conflict"] == set()
    assert flags["identical"] == {2}
    assert flags["identical_code"] == "OD_DUP_IDENTICAL"


def test_late_touchpoint_is_counted_and_flagged(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-13：入库时间晚于下单的触点仍计入，但标记迟到
    assert rows["TP-1005"]["status"] == "LATE"
    assert rows["TP-1005"]["late"] == 1
    credits = store.credits(run_id, "linear")
    assert any(row["touch_id"] == "TP-1005" and row["late"] for row in credits)


def test_negative_cost_rejected(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    assert rows["TP-1025"]["reason"] == "TD_COST_INVALID"


def test_quarantine_csv_exports_line_and_reason(client_dirty):
    response = client_dirty.get("/api/quarantine.csv")
    assert response.status_code == 200
    body = response.text
    assert "物理行" in body and "原因码" in body
    assert "TD_CHANNEL_UNKNOWN" in body
    assert body.count("\n") == DIRTY["quarantine_total"] + 1


def test_reason_distribution_matches_quarantine(store, run_id):
    counts = {row["reason"]: row["n"] for row in store.reason_counts(run_id)}
    assert counts == REASON_COUNTS
    assert sum(counts.values()) == DIRTY["quarantine_total"]


def test_four_accounts_are_balanced(dirty):
    accounts = dirty["stats"]["accounts"]
    assert accounts["touch_input"] == (accounts["touch_counted"] + accounts["touch_late"]
                                       + accounts["touch_dropped"])
    assert accounts["touch_input"] == DIRTY["touch_input"]
    assert accounts["touch_out_of_window"] == DIRTY["touch_out_of_window"]
    assert accounts["order_input"] == (accounts["order_ok"] + accounts["order_quarantined"])
    assert accounts["balanced_touch"] is True
    assert accounts["balanced_order"] is True
