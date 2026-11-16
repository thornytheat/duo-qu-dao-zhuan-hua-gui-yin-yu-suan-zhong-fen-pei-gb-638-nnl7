"""M7 重采样帧：订单去重、同秒触点全序、抽样可复现、分位数与区间端。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m1_ingest import service as m1_service
from app.modules.m7_confidence import frame, interval, resample
from app.modules.m7_confidence import service as m7_service

from fixtures.expected import DIRTY


def _touch(row_no: int, touch_id: str, iso: str, channel: str,
           event_type: str = "click", user: str = "u1") -> dict:
    return {"row_no": row_no, "touch_id": touch_id, "channel": channel,
            "event_type": event_type, "canonical_user": user,
            "event_time_utc": iso, "ingested_at_utc": iso, "late": 0,
            "status": "COUNTED"}


def _order(order_id: str, gmv_cents: int, iso: str, row_no: int = 1,
           status: str = "OK", reason: str | None = None, user: str = "u1") -> dict:
    return {"row_no": row_no, "order_id": order_id, "status": status,
            "reason": reason, "gmv_cents": gmv_cents, "canonical_user": user,
            "order_time_utc": iso, "user_id": user}


def _evidence(store, run_id: str) -> tuple[list[dict], list[dict]]:
    orders = store.orders(run_id)
    touches = [row for row in store.touchpoints(run_id)
               if row["status"] in {"COUNTED", "LATE"}]
    return orders, touches


def test_frame_keeps_one_row_per_order_and_skips_invalid(settings, store, run_id):
    orders, touches = _evidence(store, run_id)
    built = frame.build(settings, orders, touches, "time_decay")
    # TRAP-28：O-1007 两行、O-1005 金额无效，都不进重采样帧
    assert len(built["clusters"]) == DIRTY["order_ok"]
    assert built["total_cents"] == DIRTY["gmv_cents"]
    assert built["balanced"] is True
    assert {row["order_id"] for row in built["skipped"]} == {"O-1005", "O-1007"}
    assert {row["order_id"]: row["reason"] for row in built["skipped"]} == {
        "O-1005": "OD_GMV_INVALID", "O-1007": "OD_DUP_CONFLICT"}
    naive = sum(int(row["gmv_cents"] or 0) for row in orders)
    assert naive - built["total_cents"] == 19900 + 25900


def test_duplicate_order_group_counts_once(settings):
    orders = [
        _order("O-1", 10000, "2026-11-05T13:00:00Z", row_no=1),
        _order("O-1", 10000, "2026-11-05T13:00:00Z", row_no=2,
               status="QUARANTINED", reason="OD_DUP_IDENTICAL"),
        _order("O-2", 5000, "2026-11-05T14:00:00Z", row_no=3),
    ]
    touches = [_touch(1, "TP-1", "2026-11-05T10:00:00Z", "douyin_feed")]
    built = frame.build(settings, orders, touches, "linear")
    assert [cluster["order_id"] for cluster in built["clusters"]] == ["O-1", "O-2"]
    assert built["duplicate_rows"] == 1
    assert built["duplicates"] == [{"order_id": "O-1", "rows": 2, "kept_row": 1}]
    assert built["total_cents"] == 15000
    assert built["codes"] == ["CI_DUP_ORDER_KEPT"]


def test_same_second_touches_use_touch_id_tie_break(settings):
    order = _order("O-1", 10000, "2026-11-05T13:00:00Z")
    forward = [_touch(1, "TP-A", "2026-11-05T12:00:00Z", "xhs_seed"),
               _touch(2, "TP-B", "2026-11-05T12:00:00Z", "douyin_feed")]
    backward = list(reversed(forward))
    first = frame.build(settings, [order], forward, "first")["clusters"][0]
    second = frame.build(settings, [order], backward, "first")["clusters"][0]
    # TRAP-27：同一秒的两个触点按编号定序，A 在前，与证据行序无关
    assert first["channels"] == second["channels"] == {"xhs_seed": 10000}
    naive = sorted(backward, key=lambda item: item["event_time_utc"])
    assert naive[0]["touch_id"] == "TP-B"


def test_same_second_cluster_digest_is_order_independent(settings):
    order = _order("O-1", 10000, "2026-11-05T13:00:00Z")
    forward = [_touch(1, "TP-A", "2026-11-05T12:00:00Z", "xhs_seed"),
               _touch(2, "TP-B", "2026-11-05T12:00:00Z", "douyin_feed")]
    one = frame.build(settings, [order], forward, "position")
    two = frame.build(settings, [order], list(reversed(forward)), "position")
    assert one["clusters"] == two["clusters"]
    assert frame.digest(one["clusters"], "position") == frame.digest(two["clusters"], "position")


def test_window_end_touch_stays_out_of_frame(settings, store, run_id):
    orders, touches = _evidence(store, run_id)
    built = frame.build(settings, orders, touches, "time_decay")
    picked = {credit["touch_id"] for cluster in built["clusters"]
              for credit in cluster["credits"]}
    # 下单同一时刻的 TP-1004 不在 [start, end)，不会进帧
    assert "TP-1004" not in picked
    assert "TP-1003" in picked


def test_bootstrap_is_reproducible_with_same_seed(settings, store, run_id):
    orders, touches = _evidence(store, run_id)
    clusters = frame.build(settings, orders, touches, "time_decay")["clusters"]
    codes = frame.channel_codes(clusters)
    first = resample.run(clusters, codes, 64, 638)
    again = resample.run(clusters, codes, 64, 638)
    other = resample.run(clusters, codes, 64, 7)
    assert first["draws"] == again["draws"]
    assert first["draws"] != other["draws"]
    assert first["picks_per_draw"] == len(clusters)


def test_quantile_uses_floor_index():
    values = [Decimal(index) for index in range(10)]
    assert interval.quantile(values, Decimal("0.05")) == Decimal(0)
    assert interval.quantile(values, Decimal("0.50")) == Decimal(4)
    assert interval.quantile(values, Decimal("0.95")) == Decimal(8)
    assert interval.quantile([], Decimal("0.50")) is None
    assert interval.share_pct(1, 0) == Decimal("0.00")


def test_share_interval_brackets_point_estimate(settings, store, run_id):
    result = m7_service.compute(settings, store, run_id, "time_decay", 200, 638)
    assert result["status"] == "ok"
    assert result["draws"]["picks_per_draw"] == DIRTY["order_ok"]
    for row in result["channels"]:
        assert Decimal(row["share_lo"]) <= Decimal(row["share_pct"]) <= Decimal(row["share_hi"])
        assert Decimal(row["roi_lo"]) <= Decimal(row["roi"]) <= Decimal(row["roi_hi"])


def test_empty_frame_reports_reason(settings, store):
    m1_service.run(settings, store, "header_only")
    run = store.latest_run()
    result = m7_service.compute(settings, store, run["run_id"], "linear", 40, 638)
    assert result["status"] == "empty"
    assert result["clusters"] == 0
    assert [row["code"] for row in result["reasons"]] == ["CI_FRAME_EMPTY"]


def test_resamples_out_of_range_rejected(client_dirty):
    response = client_dirty.post("/api/confidence", json={"resamples": 1})
    assert response.status_code == 400
    assert response.json()["error"] == "E_BAD_REQUEST"
    assert response.json()["min"] == 20
    assert response.json()["max"] == 2000


def test_unknown_model_rejected(client_dirty):
    response = client_dirty.post("/api/confidence", json={"model": "bogus"})
    assert response.status_code == 400
    assert "time_decay" in response.json()["allowed"]
