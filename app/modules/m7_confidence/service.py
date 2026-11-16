"""M7 编排：证据 → 重采样帧 → 有放回抽样 → 区间 → 结论与预算对齐。

抽样按订单簇做，不按份额行做：一行一单被拆成多行时，按行抽会把同一单重复计入。
"""
from __future__ import annotations

from decimal import Decimal

from ... import errors, reasons
from ..m2_window.contract import CHANNEL_LABELS
from . import frame, interval, resample

DEFAULT_RESAMPLES = 200
DEFAULT_SEED = 638
DEFAULT_ALPHA_PCT = 10


def evidence(store, run_id: str) -> dict:
    touches = [touch for touch in store.touchpoints(run_id)
               if touch["status"] in {"COUNTED", "LATE"}]
    run = store.get_run(run_id) or {}
    return {"orders": store.orders(run_id), "touches": touches,
            "spend": run.get("stats", {}).get("spend", {})}


def validate(model: str, resamples: int, seed: int, alpha_pct: int) -> dict:
    from ..m4_attribution import models

    if model not in models.MODEL_CODES:
        raise errors.BadRequest(f"未知归因模型：{model}",
                                {"allowed": list(models.MODEL_CODES)})
    try:
        resamples = int(resamples)
        seed = int(seed)
        alpha_pct = int(alpha_pct)
    except (TypeError, ValueError):
        raise errors.BadRequest("重采样次数、种子、区间必须是整数",
                                {"resamples": resamples, "seed": seed})
    if not resample.MIN_RESAMPLES <= resamples <= resample.MAX_RESAMPLES:
        raise errors.BadRequest(
            f"重采样次数需在 {resample.MIN_RESAMPLES}-{resample.MAX_RESAMPLES}",
            {"resamples": resamples, "min": resample.MIN_RESAMPLES,
             "max": resample.MAX_RESAMPLES})
    if not 0 <= seed <= resample.MAX_SEED:
        raise errors.BadRequest(f"种子需在 0-{resample.MAX_SEED}", {"seed": seed})
    if not interval.MIN_ALPHA_PCT <= alpha_pct <= interval.MAX_ALPHA_PCT:
        raise errors.BadRequest(
            f"区间需在 {interval.MIN_ALPHA_PCT}-{interval.MAX_ALPHA_PCT}%",
            {"alpha_pct": alpha_pct})
    return {"model": model, "resamples": resamples, "seed": seed,
            "alpha_pct": alpha_pct}


def budget_alignment(store, run_id: str, top_channel: str | None) -> dict:
    latest = store.latest_budget(run_id)
    if not latest:
        return {"available": False, "plan_version": None, "top_channel": None,
                "top_label": None, "matches": None, "label": "无方案"}
    rows = latest["payload"].get("rows", [])
    ranked = sorted(rows, key=lambda row: (-int(row["spend_new_cents"]), row["channel"]))
    plan_top = ranked[0]["channel"] if ranked else None
    matches = plan_top == top_channel if plan_top else None
    return {"available": True, "plan_version": latest["plan_version"],
            "top_channel": plan_top,
            "top_label": CHANNEL_LABELS.get(plan_top, plan_top),
            "matches": matches, "label": "一致" if matches else "不一致"}


def compute(settings, store, run_id: str, model: str = "time_decay",
            resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED,
            alpha_pct: int = DEFAULT_ALPHA_PCT) -> dict:
    params = validate(model, resamples, seed, alpha_pct)
    loaded = evidence(store, run_id)
    framed = frame.build(settings, loaded["orders"], loaded["touches"], params["model"])
    clusters = framed["clusters"]
    codes = list(framed["codes"])
    base = {
        "run_id": run_id,
        "model": params["model"],
        "label": _model_label(params["model"]),
        "resamples": params["resamples"],
        "seed": params["seed"],
        "alpha_pct": params["alpha_pct"],
        "clusters": len(clusters),
        "frame_total_cents": int(framed["total_cents"]),
        "frame_digest": frame.digest(clusters, params["model"]),
        "balanced": bool(framed["balanced"]),
        "duplicates": framed["duplicates"],
        "skipped": framed["skipped"],
        "channels": [],
        "detail": [],
        "verdict": {"status": "—", "codes": [], "top_channel": None,
                    "top_label": None, "top_stay_pct": "0.00",
                    "max_half_width_pp": "0.00", "runner_up": None,
                    "runner_label": None},
        "reasons": [],
        "budget": budget_alignment(store, run_id, None),
        "draws": {"picks_per_draw": 0, "total_min_cents": 0, "total_max_cents": 0},
    }
    if not clusters:
        base["status"] = "empty"
        base["reasons"] = _reason_rows(["CI_FRAME_EMPTY"])
        return base

    sampled = resample.run(clusters, frame.channel_codes(clusters),
                           params["resamples"], params["seed"])
    counts = interval.top_counts(sampled["draws"])
    rows = interval.channel_rows(clusters, sampled["draws"], loaded["spend"],
                                 params["alpha_pct"])
    verdict = interval.assess(rows, counts, params["resamples"], len(clusters))
    budget = budget_alignment(store, run_id, verdict["top_channel"])
    tail = list(verdict["codes"])
    if budget["matches"] is False:
        tail.append("CI_BUDGET_MISMATCH")

    totals = [int(draw["total_cents"]) for draw in sampled["draws"]] or [0]
    base.update({
        "status": "ok",
        "channels": [_channel_payload(row, counts, params["resamples"]) for row in rows],
        "detail": _detail(clusters),
        "verdict": {
            "status": verdict["status"],
            "codes": verdict["codes"],
            "top_channel": verdict["top_channel"],
            "top_label": CHANNEL_LABELS.get(verdict["top_channel"],
                                            verdict["top_channel"]),
            "top_stay_pct": str(verdict["top_stay_pct"]),
            "max_half_width_pp": str(verdict["max_half_width_pp"]),
            "runner_up": verdict["runner_up"],
            "runner_label": CHANNEL_LABELS.get(verdict["runner_up"],
                                               verdict["runner_up"]),
        },
        "budget": budget,
        "draws": {"picks_per_draw": sampled["picks_per_draw"],
                  "total_min_cents": min(totals), "total_max_cents": max(totals)},
    })
    merged = _merge_codes(codes, tail)
    base["reasons"] = _reason_rows(merged)
    return base


def _model_label(code: str) -> str:
    from ..m4_attribution import models

    return models.label(code)


def _detail(clusters: list[dict], limit: int = 240) -> list[dict]:
    rows = []
    for cluster in clusters:
        for credit in cluster["credits"]:
            rows.append({"order_id": cluster["order_id"],
                         "touch_id": credit["touch_id"],
                         "channel": credit["channel"],
                         "label": CHANNEL_LABELS.get(credit["channel"], credit["channel"]),
                         "gmv_cents": int(credit["gmv_cents"]),
                         "position": int(credit["position"])})
    return rows[:limit]


def _channel_payload(row: dict, counts: dict, resamples: int) -> dict:
    stay = (Decimal(counts.get(row["channel"], 0)) * Decimal(100) / Decimal(int(resamples))
            if resamples else Decimal("0.00"))
    return {
        "channel": row["channel"],
        "label": CHANNEL_LABELS.get(row["channel"], row["channel"]),
        "point_cents": int(row["point_cents"]),
        "share_pct": str(row["share_pct"]),
        "share_lo": str(row["share_lo"]),
        "share_hi": str(row["share_hi"]),
        "half_width_pp": str(row["half_width_pp"]),
        "roi": "—" if row["roi"] is None else str(row["roi"]),
        "roi_lo": "—" if row["roi_lo"] is None else str(row["roi_lo"]),
        "roi_hi": "—" if row["roi_hi"] is None else str(row["roi_hi"]),
        "spend_cents": int(row["spend_cents"]),
        "top_stay_pct": str(interval.quantize(stay)),
    }


def _merge_codes(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    for code in list(first) + list(second):
        if code and code not in merged:
            merged.append(code)
    return merged


def _reason_rows(codes: list[str]) -> list[dict]:
    return [{"code": code, "label": reasons.label(code), "level": reasons.level(code)}
            for code in codes]
