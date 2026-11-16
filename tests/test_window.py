"""M2 窗口规则：半开区间、时区归一、跨设备别名合并。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.modules.m2_window import alias, coerce, window


def test_contract_lists_windows_and_models(client):
    payload = client.get("/api/contract").json()
    assert [item["interval"] for item in payload["windows"]] == ["[t-7d, t)", "[t-1d, t)"]
    assert len(payload["channels"]) == 6
    assert [item["code"] for item in payload["models"]] == [
        "first", "last", "linear", "time_decay", "position"]
    assert payload["money"]["unit"] == "分"


def test_window_start_is_inclusive(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-23：距下单正好 7 天的点击落在左端，计入
    assert rows["TP-1003"]["status"] == "COUNTED"


def test_window_end_is_exclusive(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-23：下单同一时刻的点击不在 [start, end) 内
    assert rows["TP-1004"]["status"] == "DROPPED"
    assert rows["TP-1004"]["reason"] == "OUT_OF_WINDOW"
    # 下单后 30 秒的点击同样不计入
    assert rows["TP-1011"]["status"] == "DROPPED"


def test_impression_window_is_one_day(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    assert rows["TP-1002"]["status"] == "COUNTED"
    assert rows["TP-1012"]["status"] == "DROPPED"


def test_two_timezone_forms_hit_same_instant(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-11：Z 与 +08:00 两种写法归一后是同一时刻，落同一个窗口
    assert rows["TP-1007"]["event_time_utc"] == rows["TP-1008"]["event_time_utc"]
    assert rows["TP-1007"]["status"] == "COUNTED"
    assert rows["TP-1008"]["status"] == "COUNTED"


def test_naive_timestamp_assumed_plus_eight():
    parsed = coerce.parse_ts("2026-11-05 20:30:00")
    assert parsed["kind"] == "naive"
    assert parsed["utc"] == datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc)


def test_window_bounds_are_half_open():
    class Fake:
        click_window = window.timedelta(days=7)
        impression_window = window.timedelta(days=1)

        def window_for(self, event_type):
            return self.impression_window if event_type == "impression" else self.click_window

    order_time = datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc)
    start, end = window.bounds(order_time, "click", Fake())
    assert end - start == window.timedelta(days=7)
    assert window.contains(start, end, start) is True
    assert window.contains(start, end, end) is False
    assert window.delta_days(order_time, start) == Decimal(7)


def test_impression_window_left_edge_is_inclusive():
    class Fake:
        click_window = window.timedelta(days=7)
        impression_window = window.timedelta(days=1)

        def window_for(self, event_type):
            return self.impression_window if event_type == "impression" else self.click_window

    order_time = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)
    start, end = window.bounds(order_time, "impression", Fake())
    # TRAP-23：曝光窗口 1 天，左端计入；左端前一秒与右端都不计入
    assert end - start == window.timedelta(days=1)
    assert window.contains(start, end, start) is True
    assert window.contains(start, end, start - window.timedelta(seconds=1)) is False
    assert window.contains(start, end, end) is False


def test_alias_merge_is_order_independent():
    rows = [
        {"alias_id": "d-77", "canonical_id": "u003"},
        {"alias_id": "u003", "canonical_id": "d-77"},
        {"alias_id": "d-88", "canonical_id": "d-77"},
        {"alias_id": "u003", "canonical_id": "u003"},
    ]
    forward = alias.resolve(rows)["mapping"]
    backward = alias.resolve(list(reversed(rows)))["mapping"]
    # TRAP-22：同一主体多个别名，打乱顺序结果一致，代表元取最小
    assert forward == backward
    assert forward == {"d-77": "d-77", "u003": "d-77", "d-88": "d-77"}


def test_alias_cycle_and_self_reference_merge():
    rows = [
        {"alias_id": "a", "canonical_id": "b"},
        {"alias_id": "b", "canonical_id": "c"},
        {"alias_id": "c", "canonical_id": "a"},
        {"alias_id": "b", "canonical_id": "b"},
        {"alias_id": "", "canonical_id": "x"},
    ]
    result = alias.resolve(rows)
    assert alias.rep(result["mapping"], "c") == "a"
    assert result["self_refs"] == ["b"]
    assert len(result["bad_rows"]) == 1


def test_cross_device_touch_credits_order(store, run_id):
    # 触点用设备号 d-77，订单用账号 u003，别名合并后同一个人
    credits = [row for row in store.credits(run_id, "linear") if row["touch_id"] == "TP-1013"]
    assert credits and credits[0]["order_id"] == "O-1003"
