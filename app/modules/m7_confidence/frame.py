"""重采样帧：只留有效订单，按订单簇聚合，簇内触点用全序定序。

两处朴素实现会给出不同答案：
- 同一个订单号出现多行时按行累加，一单会被计两次（TRAP-28）；
- 同秒触点只按事件时间排序，次序跟着输入行序走，首末触点与位置权重会漂移（TRAP-27）。

簇内金额仍走 M4 的权重与整数分拆分，帧只负责「哪些行算数、以什么次序算」。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from ... import reasons
from ..m2_window import service as m2_service
from ..m2_window import window
from ..m4_attribution import allocate, models


def order_key(touch: dict) -> tuple:
    """触点全序：事件时间升序，同一秒用触点编号升序。"""
    return (touch.get("event_time_utc") or "", str(touch.get("touch_id") or ""))


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def dedupe(orders: list[dict]) -> dict:
    """按订单号分组：组内取 row_no 最小的有效行，其余行不再参与重采样。"""
    groups: dict[str, list[dict]] = {}
    for order in sorted(orders, key=lambda row: int(row["row_no"])):
        key = (order.get("order_id") or "").strip()
        if key:
            groups.setdefault(key, []).append(order)

    kept, skipped, duplicates = [], [], []
    duplicate_rows = 0
    for key in sorted(groups):
        rows = groups[key]
        valid = [row for row in rows if row.get("status") == "OK"]
        if not valid:
            skipped.append({
                "order_id": key,
                "rows": len(rows),
                "reason": reasons.pick(row.get("reason") for row in rows) or "CI_FRAME_EMPTY",
            })
            continue
        kept.append(valid[0])
        if len(rows) > 1:
            duplicates.append({"order_id": key, "rows": len(rows),
                               "kept_row": valid[0]["row_no"]})
            duplicate_rows += len(rows) - 1
    return {"kept": kept, "skipped": skipped, "duplicates": duplicates,
            "duplicate_rows": duplicate_rows}


def build(settings, orders: list[dict], touches: list[dict], model: str) -> dict:
    """把一次运行的证据变成「订单簇」列表，每簇带各渠道份额。"""
    if model not in models.MODEL_CODES:
        raise ValueError(f"未知归因模型：{model}")
    deduped = dedupe(orders)
    matches = m2_service.match(settings, deduped["kept"], touches)["matches"]

    clusters = []
    for order in deduped["kept"]:
        order_time = _dt(order.get("order_time_utc"))
        picked = [item["touch"] for item in matches.get(order["order_id"], [])]
        picked.sort(key=order_key)
        gmv_cents = int(order.get("gmv_cents") or 0)
        credits, channels = [], {}
        if picked and order_time is not None and gmv_cents > 0:
            deltas = [window.delta_days(order_time, _dt(touch["event_time_utc"]))
                      for touch in picked]
            weights = models.weights(model, deltas, settings.decay_half_life_days)
            parts = allocate.split_cents(gmv_cents, weights)
            for position, (touch, cents) in enumerate(zip(picked, parts)):
                if cents <= 0:
                    continue
                code = touch.get("channel") or "—"
                channels[code] = channels.get(code, 0) + int(cents)
                credits.append({
                    "touch_id": touch["touch_id"],
                    "channel": code,
                    "gmv_cents": int(cents),
                    "position": position,
                    "event_time_utc": touch.get("event_time_utc"),
                })
        total_cents = sum(item["gmv_cents"] for item in credits)
        clusters.append({
            "order_id": order["order_id"],
            "user": order.get("canonical_user") or "",
            "gmv_cents": gmv_cents,
            "channels": channels,
            "credits": credits,
            "balanced": total_cents == gmv_cents,
        })

    duplicate_rows = int(deduped["duplicate_rows"])
    return {
        "model": model,
        "clusters": clusters,
        "duplicates": deduped["duplicates"],
        "skipped": deduped["skipped"],
        "duplicate_rows": duplicate_rows,
        "total_cents": sum(int(cluster["gmv_cents"]) for cluster in clusters),
        "balanced": all(cluster["balanced"] for cluster in clusters),
        "codes": (["CI_DUP_ORDER_KEPT"] if duplicate_rows else []) + (
            ["CI_FRAME_EMPTY"] if not clusters else []),
    }


def channel_codes(clusters: list[dict]) -> tuple[str, ...]:
    return tuple(sorted({code for cluster in clusters for code in cluster["channels"]}))


def digest(clusters: list[dict], model: str) -> str:
    """帧摘要：簇与渠道份数参与哈希，同一份证据重算得到同一个值。"""
    material = {
        "model": model,
        "clusters": [
            {"order_id": cluster["order_id"], "gmv_cents": cluster["gmv_cents"],
             "channels": dict(sorted(cluster["channels"].items()))}
            for cluster in clusters
        ],
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
