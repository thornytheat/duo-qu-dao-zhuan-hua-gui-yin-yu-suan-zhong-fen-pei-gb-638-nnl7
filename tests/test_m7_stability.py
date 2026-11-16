"""M7 结论判定与页面：区间过宽、首位不稳、区间重叠、预算对齐、接口与页面。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m7_confidence import frame, interval, resample
from app.modules.m7_confidence import service as m7_service

from fixtures.expected import DIRTY


def _row(channel: str, point_cents: int, share_pct: str, lo: str, hi: str) -> dict:
    half = (Decimal(hi) - Decimal(lo)) / Decimal(2)
    return {"channel": channel, "point_cents": point_cents,
            "share_pct": Decimal(share_pct), "share_lo": Decimal(lo),
            "share_hi": Decimal(hi), "half_width_pp": interval.quantize(half)}


def test_verdict_flags_thin_wide_and_unstable(settings, store, run_id):
    result = m7_service.compute(settings, store, run_id, "time_decay", 200, 638)
    codes = [row["code"] for row in result["reasons"]]
    # 5 单 < 8、半宽 31.05pp > 5、首位保持 36% < 90%、前两渠道区间重叠
    assert result["verdict"]["status"] == "不稳"
    assert codes[:4] == ["CI_FRAME_THIN", "CI_WIDE_INTERVAL",
                         "CI_UNSTABLE_RANK", "CI_INTERVAL_OVERLAP"]
    assert Decimal(result["verdict"]["max_half_width_pp"]) > Decimal(5)
    assert Decimal(result["verdict"]["top_stay_pct"]) < Decimal(90)


def test_verdict_is_stable_for_narrow_intervals():
    rows = [_row("a", 6000, "60.00", "58.00", "62.00"),
            _row("b", 4000, "40.00", "38.00", "42.00")]
    verdict = interval.assess(rows, {"a": 195, "b": 5}, 200, 20)
    assert verdict["status"] == "成立"
    assert verdict["codes"] == []
    assert verdict["top_channel"] == "a"
    assert verdict["runner_up"] == "b"
    assert verdict["top_stay_pct"] == Decimal("97.50")


def test_verdict_flags_wide_interval_only():
    rows = [_row("a", 5400, "54.00", "45.00", "63.00"),
            _row("b", 4600, "46.00", "30.00", "40.00")]
    verdict = interval.assess(rows, {"a": 200}, 200, 12)
    assert verdict["codes"] == ["CI_WIDE_INTERVAL"]


def test_verdict_flags_interval_overlap_only():
    rows = [_row("a", 5200, "52.00", "48.50", "55.50"),
            _row("b", 4800, "48.00", "45.00", "51.00")]
    verdict = interval.assess(rows, {"a": 200}, 200, 12)
    # TRAP-27 之后仍要看区间是否分开：两段重叠就不能说首位稳
    assert verdict["status"] == "不稳"
    assert verdict["codes"] == ["CI_INTERVAL_OVERLAP"]


def test_verdict_flags_thin_frame_only():
    rows = [_row("a", 6000, "60.00", "58.00", "62.00"),
            _row("b", 4000, "40.00", "38.00", "42.00")]
    verdict = interval.assess(rows, {"a": 200}, 200, 5)
    assert verdict["codes"] == ["CI_FRAME_THIN"]


def test_every_draw_conserves_cluster_total(settings, store, run_id):
    orders = store.orders(run_id)
    touches = [row for row in store.touchpoints(run_id)
               if row["status"] in {"COUNTED", "LATE"}]
    clusters = frame.build(settings, orders, touches, "linear")["clusters"]
    sampled = resample.run(clusters, frame.channel_codes(clusters), 32, 638)
    source = resample.stream(638)
    # 不变量：每次抽样的渠道合计 = 该次抽到的订单簇成交额合计
    for draw in sampled["draws"]:
        picks = [next(source) % len(clusters) for _ in range(len(clusters))]
        expected = sum(int(clusters[index]["gmv_cents"]) for index in picks)
        assert draw["total_cents"] == expected
        assert sum(draw["channels"].values()) == expected
    assert sampled["draws"]


def test_confidence_api_returns_intervals(client_dirty):
    response = client_dirty.post("/api/confidence",
                                 json={"model": "linear", "resamples": 100, "seed": 11})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["clusters"] == DIRTY["order_ok"]
    assert body["frame_total_cents"] == DIRTY["gmv_cents"]
    assert body["balanced"] is True
    assert len(body["channels"]) == 6
    for row in body["channels"]:
        assert Decimal(row["share_lo"]) <= Decimal(row["share_hi"])
        assert row["top_stay_pct"]
    assert body["verdict"]["status"] in {"成立", "不稳"}
    assert body["budget"]["label"] == "无方案"
    assert body["detail"]


def test_budget_alignment_and_mismatch(client_dirty):
    client_dirty.post("/api/budgets", json={"plan_version": 1, "total_yuan": 60000})
    aligned = client_dirty.get(
        "/api/confidence?model=time_decay&resamples=100&seed=638").json()
    assert aligned["budget"]["available"] is True
    assert aligned["budget"]["plan_version"] == 1
    assert aligned["budget"]["top_channel"] == "douyin_feed"
    assert aligned["budget"]["matches"] is True
    assert aligned["budget"]["label"] == "一致"
    assert "CI_BUDGET_MISMATCH" not in [row["code"] for row in aligned["reasons"]]

    shifted = client_dirty.get(
        "/api/confidence?model=first&resamples=100&seed=638").json()
    assert shifted["verdict"]["top_channel"] == "xhs_seed"
    assert shifted["budget"]["matches"] is False
    assert "CI_BUDGET_MISMATCH" in [row["code"] for row in shifted["reasons"]]


def test_confidence_page_renders(client_dirty):
    response = client_dirty.get("/confidence?model=time_decay")
    assert response.status_code == 200
    assert 'data-page="confidence"' in response.text
    for text in ("稳健性结论", "渠道区间", "未入帧订单", "帧明细", "首位保持"):
        assert text in response.text
    assert "douyin_feed" in response.text


def test_confidence_page_empty_state(client):
    response = client.get("/confidence")
    assert response.status_code == 200
    assert "还没有可重采样的订单。" in response.text


def test_confidence_without_run_404(client):
    response = client.get("/api/confidence")
    assert response.status_code == 404
    assert response.json()["error"] == "E_RUN_NOT_FOUND"


def test_page_reports_bad_resamples_in_place(client_dirty):
    response = client_dirty.get("/confidence?model=linear&resamples=1&seed=638")
    assert response.status_code == 200
    assert "重采样次数需在 20-2000" in response.text
    assert 'value="1"' in response.text
    assert "渠道区间" in response.text
