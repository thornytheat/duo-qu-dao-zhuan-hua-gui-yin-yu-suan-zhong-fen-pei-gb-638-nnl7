"""M3 编排：按优先级定码 → 隔离落盘 → 窗口匹配 → 四账。"""
from __future__ import annotations

from decimal import Decimal

from ... import reasons
from ..m2_window import contract as m2_contract
from ..m2_window import service as m2_service
from . import quarantine, rules


def evaluate(settings, normalized: dict) -> dict:
    touches = normalized["touches"]
    orders = normalized["orders"]
    quarantined = list(normalized["quarantined"])

    touch_dup = rules.duplicate_flags("touch", touches)
    order_dup = rules.duplicate_flags("order", orders)

    for touch in touches:
        codes = rules.touch_codes(touch, settings)
        if touch["row_no"] in touch_dup["conflict"]:
            codes.append(touch_dup["conflict_code"])
        elif touch["row_no"] in touch_dup["identical"]:
            codes.append(touch_dup["identical_code"])
        code = reasons.pick(codes)
        if code:
            touch["status"] = "DROPPED"
            touch["reason"] = code
            quarantined.append(quarantine.record(
                "touchpoint", touch, code, quarantine.raw_of("touch", touch)))
        else:
            touch["status"] = "OK"
            touch["reason"] = None

    for order in orders:
        codes = rules.order_codes(order)
        if order["row_no"] in order_dup["conflict"]:
            codes.append(order_dup["conflict_code"])
        elif order["row_no"] in order_dup["identical"]:
            codes.append(order_dup["identical_code"])
        code = reasons.pick(codes)
        if code:
            order["status"] = "QUARANTINED"
            order["reason"] = code
            quarantined.append(quarantine.record(
                "order", order, code, quarantine.raw_of("order", order)))
        else:
            order["status"] = "OK"
            order["reason"] = None

    kept_touches = [touch for touch in touches if touch["status"] == "OK"]
    ok_orders = [order for order in orders if order["status"] == "OK"]
    matched = m2_service.match(settings, ok_orders, kept_touches)

    for touch in touches:
        if touch["status"] != "OK":
            continue
        state = matched["status"].get(touch["row_no"])
        if state is None:
            touch["status"] = "DROPPED"
            touch["reason"] = "OUT_OF_WINDOW"
            continue
        touch["status"] = state["status"]
        touch["reason"] = state["reason"]
        touch["late"] = bool(state["late"])

    matched_touches = [touch for touch in touches
                       if touch["status"] in {"COUNTED", "LATE"}]
    matches = {
        order_id: [item["touch"]["row_no"] for item in items]
        for order_id, items in matched["matches"].items()
    }

    gmv_cents = sum(int(order["gmv_cents"] or 0) for order in ok_orders)
    acc = quarantine.accounts(touches, orders, quarantined)
    return {
        "touches": touches,
        "orders": orders,
        "quarantined": quarantined,
        "kept_touches": matched_touches,
        "ok_orders": ok_orders,
        "matches": matches,
        "accounts": acc,
        "spend": spend_by_channel(touches),
        "summary": summary(matched_touches, orders, acc, gmv_cents),
        "reason_distribution": quarantine.distribution(quarantined),
        "channels": [{"code": code, "label": label}
                     for code, label in m2_contract.CHANNELS],
    }


def spend_by_channel(touches: list[dict]) -> dict:
    """各渠道花费：只算计入与迟到的触点。"""
    result: dict[str, dict] = {}
    for touch in touches:
        if touch["status"] not in {"COUNTED", "LATE"}:
            continue
        channel = touch["channel"] or "—"
        bucket = result.setdefault(channel, {"channel": channel,
                                             "label": m2_contract.CHANNEL_LABELS.get(
                                                 channel, channel),
                                             "cost_cents": 0, "n": 0})
        bucket["cost_cents"] += int(touch["cost_cents"] or 0)
        bucket["n"] += 1
    return result


def summary(touches: list[dict], orders: list[dict], accounts: dict,
            gmv_cents: int) -> dict:
    total = accounts["touch_input"]
    kept = accounts["touch_counted"] + accounts["touch_late"]
    return {
        "pass_rate_pct": round(kept * 100.0 / total, 1) if total else 0.0,
        "quarantine_rate_pct": round((total - kept) * 100.0 / total, 1) if total else 0.0,
        "gmv_cents": gmv_cents,
        "orders_ok": accounts["order_ok"],
        "touches_kept": kept,
        "touches_dropped": accounts["touch_dropped"],
        "avg_gmv_cents": int(Decimal(gmv_cents) / accounts["order_ok"])
        if accounts["order_ok"] else 0,
    }
