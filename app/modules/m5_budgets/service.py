"""M5 编排：约束读取 → 边际 ROI 贪心 → 版本校验 → 落库。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from ... import errors, manifest
from ..m2_window.contract import CHANNEL_LABELS
from . import batches, greedy, roi as roi_rules

DEFAULT_CONSTRAINTS = {"min_yuan": 1000, "cap_yuan": 30000}


def constraints_path(settings):
    return settings.sample_dir / "budget_plan.json"


def load_constraints(settings) -> dict:
    path = constraints_path(settings)
    if not path.exists():
        payload = {"total_yuan": 60000,
                   "channels": {code: dict(DEFAULT_CONSTRAINTS) for code in CHANNEL_LABELS}}
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    channels = {}
    for code, label in CHANNEL_LABELS.items():
        item = payload.get("channels", {}).get(code, {})
        channels[code] = {
            "label": label,
            "min_cents": int(Decimal(str(item.get("min_yuan", 1000))) * 100),
            "cap_cents": int(Decimal(str(item.get("cap_yuan", 30000))) * 100),
        }
    return {"total_cents": int(Decimal(str(payload.get("total_yuan", 60000))) * 100),
            "channels": channels,
            "digest": manifest.file_digest(path) if path.exists() else None,
            "path": str(path)}


def channel_rois(store, run_id: str, model: str, spend: dict,
                 net_gmv: dict | None = None) -> dict:
    """渠道基础 ROI。

    net_gmv 为 None 时用原归因成交额（历史口径，旧版本依据）；
    传入退款结转后的净额时，ROI 只作用于当前这一版新建议，已审批版本不改写。
    """
    totals = store.channel_totals(run_id, model)
    rois = {}
    for code in CHANNEL_LABELS:
        if net_gmv is not None:
            gmv = int(net_gmv.get(code, 0))
        else:
            gmv = next((int(row["gmv"] or 0) for row in totals
                        if row["channel"] == code), 0)
        cost = int(spend.get(code, {}).get("cost_cents", 0))
        rois[code] = roi_rules.base_roi(gmv, cost)
    return rois


def reallocate(settings, store, run_id: str, plan_version: int,
               total_cents: int, model: str = "time_decay",
               use_refund_net: bool = False) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise errors.NotFoundRun(f"运行不存在：{run_id}")
    if total_cents <= 0:
        raise errors.BadRequest("预算必须大于 0")
    batches.check_version(store, run_id, plan_version)

    plan = load_constraints(settings)
    spend_before = run["stats"].get("spend", {})
    net_gmv = None
    refund_view = None
    if use_refund_net:
        from ..m8_refunds import service as m8_service
        refund_view = m8_service.carry_forward(store, run_id, model)
        net_gmv = {row["channel"]: row["net_cents"]
                   for row in refund_view["channels"]}
    rois = channel_rois(store, run_id, model, spend_before, net_gmv)
    result = greedy.allocate(total_cents, plan["channels"], rois,
                             settings.budget_step_cents, settings.marginal_decay)

    rows = []
    for code in sorted(plan["channels"]):
        info = plan["channels"][code]
        new_cents = int(result["spend"][code])
        before_cents = int(spend_before.get(code, {}).get("cost_cents", 0))
        rows.append({
            "channel": code,
            "label": info["label"],
            "min_cents": info["min_cents"],
            "cap_cents": info["cap_cents"],
            "spend_before_cents": before_cents,
            "spend_new_cents": new_cents,
            "delta_cents": new_cents - before_cents,
            "base_roi": str(rois[code]),
            "marginal_roi": str(roi_rules.marginal_roi(
                rois[code], int(result["steps"][code]), settings.marginal_decay)),
            "steps": int(result["steps"][code]),
            "share_pct": round(new_cents * 100.0 / total_cents, 2),
            "hit": ("上限" if new_cents >= info["cap_cents"] else
                    ("下限" if new_cents <= info["min_cents"] else "—")),
        })
    payload = {
        "run_id": run_id,
        "plan_version": int(plan_version),
        "model": model,
        "total_cents": int(total_cents),
        "balanced": sum(row["spend_new_cents"] for row in rows) == int(total_cents),
        "rows": rows,
        "trace_count": len(result["trace"]),
        "constraints_digest": plan["digest"],
        "roi_basis": "refund_net" if use_refund_net else "original_gmv",
        "refund_net_cents": (refund_view["totals"] if refund_view else None),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    store.save_budget(run_id, plan_version, total_cents, payload, payload["created_at"])
    return payload
