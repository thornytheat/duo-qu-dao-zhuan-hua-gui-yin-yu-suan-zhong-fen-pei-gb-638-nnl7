"""M4 归因计算：五模型守恒、整数分拆分、衰减与位置权重。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m4_attribution import allocate, decay, service

from fixtures.expected import DIRTY, make_order, make_touch


def _credits(model, touches, order, settings):
    matches = {order["order_id"]: [touch["row_no"] for touch in touches]}
    result = service.attribute(settings, [order], touches, matches, {})
    return [row for row in result["credits"] if row["model"] == model]


def _three_touches():
    return [
        make_touch(1, "T1", "2026-11-05T10:00:00Z"),
        make_touch(2, "T2", "2026-11-05T11:00:00Z", channel="xhs_seed"),
        make_touch(3, "T3", "2026-11-05T12:00:00Z", channel="baidu_search"),
    ]


def test_five_models_conserve_order_gmv(dirty):
    totals = dirty["stats"]["model_totals"]
    # TRAP-10：份额用整数分，五个模型各自合计都等于订单成交额
    assert dirty["stats"]["gmv_cents"] == DIRTY["gmv_cents"]
    assert set(totals) == {"first", "last", "linear", "time_decay", "position"}
    assert {value for value in totals.values()} == {DIRTY["gmv_cents"]}
    assert dirty["stats"]["reconcile"]["gmv_balanced"] is True


def test_split_cents_is_exact_for_thirds():
    parts = allocate.split_cents(1000, [Decimal(1), Decimal(1), Decimal(1)])
    # TRAP-24：最大余额法补足尾差，合计精确等于原额
    assert parts == [334, 333, 333]
    assert sum(parts) == 1000


def test_first_and_last_models_pick_single_touch(settings):
    order = make_order("O-1", 10000, "2026-11-05T13:00:00Z")
    touches = _three_touches()
    first = _credits("first", touches, order, settings)
    last = _credits("last", touches, order, settings)
    assert [(row["touch_id"], row["gmv_cents"]) for row in first] == [("T1", 10000)]
    assert [(row["touch_id"], row["gmv_cents"]) for row in last] == [("T3", 10000)]


def test_linear_model_splits_evenly(settings):
    order = make_order("O-1", 10000, "2026-11-05T13:00:00Z")
    rows = _credits("linear", _three_touches(), order, settings)
    assert [row["gmv_cents"] for row in rows] == [3334, 3333, 3333]
    assert sum(row["gmv_cents"] for row in rows) == 10000


def test_same_second_touches_break_ties_by_touch_id(settings):
    order = make_order("O-1", 10000, "2026-11-05T13:00:00Z")
    touches = [
        make_touch(1, "TP-B", "2026-11-05T12:00:00Z", channel="douyin_feed"),
        make_touch(2, "TP-A", "2026-11-05T12:00:00Z", channel="xhs_seed"),
    ]
    forward = service.attribute(settings, [order], touches, {"O-1": [1, 2]}, {})
    backward = service.attribute(settings, [order], list(reversed(touches)),
                                 {"O-1": [2, 1]}, {})
    first = [row for row in forward["credits"] if row["model"] == "first"]
    # TRAP-27 上游：同一秒按触点编号定序，首触点固定在 TP-A，与证据行序无关
    assert [(row["touch_id"], row["channel"], row["gmv_cents"]) for row in first] == [
        ("TP-A", "xhs_seed", 10000)]
    assert forward["credits"] == backward["credits"]


def test_position_model_uses_forty_forty_twenty():
    assert decay.position(4) == [Decimal("0.4"), Decimal("0.1"),
                                 Decimal("0.1"), Decimal("0.4")]
    assert decay.position(1) == [Decimal(1)]
    assert decay.position(2) == [Decimal("0.5"), Decimal("0.5")]


def test_time_decay_halves_at_half_life():
    weights = decay.time_decay([Decimal(0), Decimal("3.5"), Decimal(7)], Decimal("3.5"))
    assert weights[1] == Decimal("0.5")
    assert weights[2] == Decimal("0.25")
    assert weights[0] > weights[1] > weights[2]


def test_single_touch_gets_full_credit(settings):
    order = make_order("O-1", 10000, "2026-11-05T13:00:00Z")
    touches = [make_touch(1, "T1", "2026-11-05T10:00:00Z")]
    for model in ("first", "last", "linear", "time_decay", "position"):
        rows = _credits(model, touches, order, settings)
        assert [row["gmv_cents"] for row in rows] == [10000]


def test_naive_float_split_loses_a_cent():
    # 反例：朴素实现逐份四舍五入，3 份合计只有 999 分
    naive = [round(1000 / 3) for _ in range(3)]
    assert sum(naive) == 999
    parts = allocate.split_cents(1000, [Decimal(1), Decimal(1), Decimal(1)])
    assert sum(parts) == 1000


def test_unknown_model_returns_400(client_dirty):
    response = client_dirty.get("/api/attribution?model=bogus")
    assert response.status_code == 400
    assert response.json()["error"] == "E_BAD_REQUEST"
    assert "time_decay" in response.json()["allowed"]
