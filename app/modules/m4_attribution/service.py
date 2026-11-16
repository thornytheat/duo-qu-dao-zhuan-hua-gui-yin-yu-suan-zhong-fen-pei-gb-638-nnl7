"""M4 编排：逐单拆份额 → 落证据行 → 渠道汇总与 ROI。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..m2_window import window
from . import allocate, models


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def attribute(settings, orders, touches, matches, spend: dict) -> dict:
    by_row = {touch["row_no"]: touch for touch in touches}
    credits: list[dict] = []
    model_totals: dict[str, int] = {}

    for model in models.MODEL_CODES:
        total = 0
        for order in orders:
            order_time = _dt(order["order_time_utc"])
            picked = [by_row[row_no] for row_no in matches.get(order["order_id"], [])
                      if row_no in by_row]
            picked.sort(key=lambda item: (item["event_time_utc"], item["touch_id"]))
            if not picked or order_time is None:
                continue
            deltas = [window.delta_days(order_time, _dt(item["event_time_utc"]))
                      for item in picked]
            weights = models.weights(model, deltas, settings.decay_half_life_days)
            parts = allocate.split_cents(order["gmv_cents"], weights)
            for position, (touch, cents) in enumerate(zip(picked, parts)):
                if cents <= 0:
                    continue
                credits.append({
                    "model": model,
                    "order_id": order["order_id"],
                    "touch_id": touch["touch_id"],
                    "channel": touch["channel"],
                    "gmv_cents": cents,
                    "position": position,
                    "late": bool(touch["late"]),
                })
                total += cents
        model_totals[model] = total

    return {
        "credits": credits,
        "model_totals": model_totals,
        "models": summarize(model_totals, credits, spend, orders),
    }


def summarize(model_totals: dict[str, int], credits: list[dict], spend: dict,
              orders: list[dict]) -> list[dict]:
    order_total = sum(int(order["gmv_cents"] or 0) for order in orders)
    result = []
    for code, label in models.MODELS:
        total = int(model_totals.get(code, 0))
        rows = [row for row in credits if row["model"] == code]
        channels = {}
        for row in rows:
            bucket = channels.setdefault(row["channel"], {"gmv_cents": 0, "n": 0})
            bucket["gmv_cents"] += int(row["gmv_cents"])
            bucket["n"] += 1
        channel_rows = []
        for channel, bucket in channels.items():
            spend_cents = int(spend.get(channel, {}).get("cost_cents", 0))
            channel_rows.append({
                "channel": channel,
                "gmv_cents": bucket["gmv_cents"],
                "rows": bucket["n"],
                "share_pct": round(bucket["gmv_cents"] * 100.0 / total, 2) if total else 0.0,
                "spend_cents": spend_cents,
                "roi": (round(bucket["gmv_cents"] / spend_cents, 2) if spend_cents else None),
            })
        channel_rows.sort(key=lambda item: (-item["gmv_cents"], item["channel"]))
        result.append({
            "code": code,
            "label": label,
            "gmv_cents": total,
            "order_total_cents": order_total,
            "balanced": total == order_total,
            "rows": len(rows),
            "orders": len({row["order_id"] for row in rows}),
            "channels": channel_rows,
        })
    return result


def aggregates(store, run_id: str, model: str) -> dict:
    if model not in models.MODEL_CODES:
        raise ValueError(f"未知归因模型：{model}")
    channels = store.channel_totals(run_id, model)
    totals = store.model_totals(run_id)
    run = store.get_run(run_id)
    spend = (run or {}).get("stats", {}).get("spend", {})
    order_total = int((run or {}).get("stats", {}).get("gmv_cents", 0))
    total = int(totals.get(model, {}).get("gmv_cents", 0))
    rows = []
    for row in channels:
        spend_cents = int(spend.get(row["channel"], {}).get("cost_cents", 0))
        rows.append({
            "channel": row["channel"],
            "gmv_cents": int(row["gmv"] or 0),
            "rows": row["n"],
            "late_rows": row["late_n"],
            "share_pct": (round(int(row["gmv"] or 0) * 100.0 / total, 2) if total else 0.0),
            "spend_cents": spend_cents,
            "roi": (round(int(row["gmv"] or 0) / spend_cents, 2) if spend_cents else None),
        })
    rows.sort(key=lambda item: (-item["gmv_cents"], item["channel"]))
    return {
        "run_id": run_id,
        "model": model,
        "label": models.label(model),
        "gmv_cents": total,
        "order_total_cents": order_total,
        "balanced": total == order_total,
        "channels": rows,
        "totals": {code: {"gmv_cents": int(value.get("gmv_cents", 0)),
                          "rows": int(value.get("rows", 0)),
                          "balanced": int(value.get("gmv_cents", 0)) == order_total}
                   for code, value in totals.items()},
        "credits": store.credits(run_id, model),
    }
