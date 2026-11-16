"""M8 编排：退款批次解析 → 幂等校验 → 断点续跑 → 按原单份额冲减 → 对账。

不可变原则：orders / credits / budgets / logs 一律不改写。退款证据落在独立的
refund_batches / refund_rows / refund_allocations 三张表，差异只通过「调整后视图」
影响后续预算建议，已执行花费与已审批预算版本保持原样。

批次状态机：RUNNING（含断点）→ APPLIED / PARTIAL / FAILED。
- 同一 batch_id 内容一致：RUNNING 可续跑，APPLIED/PARTIAL 直接回放（幂等）；
- 同一 batch_id 内容冲突：409，拒绝；
- 处理中抛错：批次标 FAILED，已写入的行/份额回滚，旧版本与旧批次不受影响。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ... import errors, manifest
from ..m2_window.contract import CHANNEL_LABELS
from ..m4_attribution import models as m4_models
from . import allocate, parse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def refund_file_paths(settings, name: str) -> Path:
    """退款样例文件：sample_data/refunds/<name>.csv，名字只允许安全字符。"""
    safe = Path(name).name
    if safe != name or not safe.endswith(".csv"):
        raise errors.BadRequest(f"非法退款文件名：{name}")
    path = Path(settings.sample_dir) / "refunds" / safe
    if not path.exists():
        raise errors.DatasetUnavailable(f"退款文件不存在：{name}")
    return path


def _load_rows(settings, source: str, content: str | None) -> tuple[str, list[dict], dict]:
    if content is not None:
        text = content
        origin = "inline"
    else:
        path = refund_file_paths(settings, source)
        text = path.read_text(encoding="utf-8")
        origin = path.name
    normalized = parse.normalize(parse.parse_text(text))
    return origin, normalized["rows"], {
        "blanks": normalized["blanks"], "dup_headers": normalized["dup_headers"],
        "truncated": normalized["truncated"], "mismatches": normalized["mismatches"],
        "digest": manifest.file_digest(refund_file_paths(settings, source))
        if content is None else None,
    }


def _duplicate_codes(rows: list[dict]) -> None:
    """同批次内 refund_id 重复：内容相同只留首条，冲突整行隔离。"""
    seen: dict[str, dict] = {}
    for row in rows:
        if row["status"] == "QUARANTINED" or not row["refund_id"]:
            continue
        previous = seen.get(row["refund_id"])
        if previous is None:
            seen[row["refund_id"]] = row
            continue
        same = (previous["order_id"] == row["order_id"]
                and previous["refund_cents"] == row["refund_cents"]
                and previous["refund_time_utc"] == row["refund_time_utc"])
        row["status"] = "QUARANTINED"
        row["reason"] = "RF_ROW_DUP_IDENTICAL" if same else "RF_ROW_DUP_CONFLICT"


def _check_batch_conflict(store, run_id: str, batch_id: str, digest: str) -> dict | None:
    existing = store.get_refund_batch(run_id, batch_id)
    if existing is None:
        return None
    if existing.get("content_digest") and existing["content_digest"] != digest:
        raise errors.RefundConflict(
            f"退款批次 {batch_id} 已存在且内容不一致，禁止覆盖",
            {"batch_id": batch_id, "submitted_digest": digest,
             "stored_digest": existing["content_digest"],
             "stored_status": existing["status"]})
    return existing


def _merge_checkpoint(store, run_id: str, batch_id: str, rows: list[dict]) -> list[dict]:
    """把库里上一轮的行状态回灌到新解析的行，断点续跑才能认出已冲减行。

    匹配键是行内容指纹：同内容重试时行序与归一值不变，指纹一致；
    指纹对不上的行按新行处理（整批内容指纹已先做过一致性校验，正常不会走到）。
    """
    saved = {row["content_digest"]: row for row in store.refund_rows(run_id, batch_id)}
    merged = []
    for row in rows:
        previous = saved.get(row["content_digest"])
        if previous is not None and previous["status"] in {"APPLIED", "QUARANTINED"}:
            row["status"] = previous["status"]
            row["reason"] = previous["reason"]
            row["applied"] = bool(previous["applied"])
        merged.append(row)
    return merged


def process_batch(settings, store, run_id: str, batch_id: str,
                  source: str = "", content: str | None = None) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise errors.NotFoundRun(f"运行不存在：{run_id}")
    if not batch_id or not str(batch_id).strip():
        raise errors.BadRequest("退款批次号不能为空")

    origin, rows, file_meta = _load_rows(settings, source, content)
    _duplicate_codes(rows)
    digest = parse.batch_digest(rows)
    existing = _check_batch_conflict(store, run_id, batch_id, digest)

    # 幂等回放：已终态且内容一致，直接返回既有结果，绝不二次冲减
    if existing is not None and existing["status"] in {"APPLIED", "PARTIAL"}:
        return _response(store, run_id, existing, resumed=False, idempotent=True)
    if existing is not None:
        # RUNNING 中断或 FAILED 重试：清掉上一轮份额残骸，回灌行级检查点，
        # 已 APPLIED 的行跳过不重复冲减，从断点继续
        store.replace_refund_allocations(run_id, batch_id, [])
        rows = _merge_checkpoint(store, run_id, batch_id, rows)

    started_at = existing["created_at"] if existing else _now()
    batch = {
        "batch_id": batch_id, "status": "RUNNING", "source": origin,
        "total_rows": len(rows), "processed_rows": 0, "applied_rows": 0,
        "quarantined_rows": sum(1 for r in rows if r["status"] == "QUARANTINED"),
        "refund_cents": 0, "payload": {"started_at": started_at, "file": file_meta},
        "created_at": started_at, "updated_at": _now(), "error": None,
        "content_digest": digest,
    }
    store.save_refund_batch(run_id, batch)

    try:
        result = _process_rows(store, run_id, batch_id, rows)
    except Exception as exc:  # 失败保留旧版：本批残骸清掉，批次标 FAILED
        store.replace_refund_allocations(run_id, batch_id, [])
        batch.update({"status": "FAILED", "updated_at": _now(),
                      "error": str(exc), "payload": {**batch["payload"], "failed_at": _now()}})
        store.save_refund_batch(run_id, batch)
        if isinstance(exc, errors.DomainError):
            raise
        raise errors.RefundInfeasible(f"退款批次处理失败：{exc}") from exc

    applied_rows = result["applied_rows"]
    quarantined = len(rows) - applied_rows
    final_status = "APPLIED" if quarantined == 0 else "PARTIAL"
    batch.update({
        "status": final_status,
        "processed_rows": len(rows),
        "applied_rows": applied_rows,
        "quarantined_rows": quarantined,
        "refund_cents": result["refund_cents"],
        "updated_at": _now(),
        "payload": {**batch["payload"], "finished_at": _now(),
                    "allocations": result["allocations_count"],
                    "quarantine_codes": result["codes"]},
    })
    store.save_refund_batch(run_id, batch)
    saved = store.get_refund_batch(run_id, batch_id)
    return _response(store, run_id, saved, resumed=existing is not None, idempotent=False)


def _process_rows(store, run_id: str, batch_id: str, rows: list[dict]) -> dict:
    """逐行处理，行级检查点保证中断后从断点续跑、重试不重复冲减。

    份额在最后从「全部 APPLIED 行」统一重建：入口已清掉本批旧份额，
    因此无论上次中断在哪里，续跑都能得到完整且不重复的拆分。
    """
    orders = {row["order_id"]: row for row in store.orders(run_id)}
    # 已应用退款按订单汇总，续跑时排除本批次自身（本批残骸已在入口清理）
    already = store.applied_refund_total_by_order(run_id, exclude_batch=batch_id)
    # 模型 → 订单 → 该订单的份额行，全部来自既有 credits，历史不重算
    credit_index: dict[str, dict[str, list[dict]]] = {}
    for model in m4_models.MODEL_CODES:
        per_order: dict[str, list[dict]] = {}
        for credit in store.credits(run_id, model):
            per_order.setdefault(credit["order_id"], []).append(credit)
        credit_index[model] = per_order

    applied: list[dict] = []
    applied_rows = 0
    refund_total = 0
    codes: dict[str, int] = {}

    def mark(row: dict, status: str, reason: str | None) -> None:
        row["status"] = status
        row["reason"] = reason
        row["applied"] = status == "APPLIED"
        store.upsert_refund_row(run_id, batch_id, row)

    for row in rows:
        # 断点续跑：已 APPLIED 的行直接跳过，不重复冲减
        if row["status"] == "APPLIED":
            applied.append(row)
            applied_rows += 1
            refund_total += int(row["refund_cents"])
            continue
        if row["status"] == "QUARANTINED":
            codes[row["reason"]] = codes.get(row["reason"], 0) + 1
            store.upsert_refund_row(run_id, batch_id, row)
            continue

        order = orders.get(row["order_id"])
        if order is None:
            mark(row, "QUARANTINED", "RF_ORDER_UNKNOWN")
        elif order.get("status") != "OK":
            mark(row, "QUARANTINED", "RF_ORDER_INVALID")
        else:
            prior = int(already.get(row["order_id"], 0))
            gmv = int(order["gmv_cents"] or 0)
            if prior + int(row["refund_cents"]) > gmv:
                mark(row, "QUARANTINED", "RF_OVER_REFUND")
            else:
                already[row["order_id"]] = prior + int(row["refund_cents"])
                mark(row, "APPLIED", None)
                applied.append(row)
                applied_rows += 1
                refund_total += int(row["refund_cents"])
                continue
        codes[row["reason"]] = codes.get(row["reason"], 0) + 1

    allocations: list[dict] = []
    for row in applied:
        allocations.extend(_allocate_row(batch_id, row, credit_index))
    # 份额行整批替换：与行检查点配合，重试不会留下重复份额
    store.replace_refund_allocations(run_id, batch_id, allocations)
    return {"applied_rows": applied_rows, "refund_cents": refund_total,
            "allocations_count": len(allocations), "codes": codes}


def _allocate_row(batch_id: str, row: dict,
                  credit_index: dict[str, dict[str, list[dict]]]) -> list[dict]:
    allocations = []
    for model in m4_models.MODEL_CODES:
        order_credits = credit_index[model].get(row["order_id"], [])
        parts = allocate.split_refund(row["refund_cents"], order_credits)
        for part in parts:
            allocations.append({
                "batch_id": batch_id, "refund_id": row["refund_id"], "model": model,
                "order_id": row["order_id"], "touch_id": part["touch_id"],
                "channel": part["channel"], "cents": part["cents"],
                "position": part["position"],
            })
    return allocations


def _response(store, run_id: str, batch: dict, *, resumed: bool,
              idempotent: bool) -> dict:
    rows = store.refund_rows(run_id, batch["batch_id"])
    return {
        "run_id": run_id, "batch_id": batch["batch_id"], "status": batch["status"],
        "total_rows": batch["total_rows"], "processed_rows": batch["processed_rows"],
        "applied_rows": batch["applied_rows"],
        "quarantined_rows": batch["quarantined_rows"],
        "refund_cents": batch["refund_cents"],
        "resumed": resumed, "idempotent": idempotent,
        "error": batch.get("error"),
        "rows": [{"row_no": r["row_no"], "refund_id": r["refund_id"],
                  "order_id": r["order_id"], "refund_cents": r["refund_cents"],
                  "status": r["status"], "reason": r["reason"]} for r in rows],
    }


# ---- 只读视图：结转后的净额、调整 ROI 与对账，不改任何历史 ----

def carry_forward(store, run_id: str, model: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise errors.NotFoundRun(f"运行不存在：{run_id}")
    if model not in m4_models.MODEL_CODES:
        raise errors.BadRequest(f"未知归因模型：{model}",
                                {"allowed": list(m4_models.MODEL_CODES)})
    spend = run["stats"].get("spend", {})
    original = {row["channel"]: int(row["gmv"] or 0)
                for row in store.channel_totals(run_id, model)}
    refunds = {row["channel"]: int(row["cents"])
               for row in store.refund_channel_totals(run_id, model)}

    channels = []
    for code in CHANNEL_LABELS:
        gmv = original.get(code, 0)
        refunded = refunds.get(code, 0)
        net = gmv - refunded
        cost = int(spend.get(code, {}).get("cost_cents", 0))
        channels.append({
            "channel": code, "label": CHANNEL_LABELS[code],
            "gmv_cents": gmv, "refund_cents": refunded, "net_cents": net,
            "spend_cents": cost,
            "roi": (round(gmv / cost, 2) if cost else None),
            "adjusted_roi": (round(net / cost, 2) if cost else None),
        })
    channels.sort(key=lambda item: (-item["net_cents"], item["channel"]))

    refund_total = sum(item["refund_cents"] for item in channels)
    gmv_total = sum(item["gmv_cents"] for item in channels)
    net_total = sum(item["net_cents"] for item in channels)
    order_gmv = store.order_gmv_total(run_id)
    applied_order_refund = sum(
        store.applied_refund_total_by_order(run_id).values())
    spend_total = sum(item["spend_cents"] for item in channels)
    return {
        "run_id": run_id, "model": model, "label": m4_models.label(model),
        "channels": channels,
        "totals": {
            "gmv_cents": gmv_total,
            "refund_cents": refund_total,
            "net_cents": net_total,
            "order_gmv_cents": order_gmv,
            "order_refund_cents": applied_order_refund,
            "spend_cents": spend_total,
        },
        "reconcile": reconcile(store, run_id, model),
    }


def reconcile(store, run_id: str, model: str) -> dict:
    """四张账对平：退款、订单净额、渠道花费、（下一版）预算余额。"""
    run = store.get_run(run_id)
    spend = run["stats"].get("spend", {})
    # 1) 退款账：批次行金额合计 = 份额拆分合计（按模型，任一模型都应等于退款总额）
    row_refund = sum(int(r["refund_cents"]) for r in store.refund_rows(run_id)
                     if r["status"] == "APPLIED")
    alloc_refund = sum(int(r["cents"]) for r in store.refund_allocations(run_id, model))
    refund_balanced = row_refund == alloc_refund
    # 2) 订单净额账：原成交额 - 订单维度退款 = 渠道净额合计
    order_gmv = store.order_gmv_total(run_id)
    order_refund = sum(store.applied_refund_total_by_order(run_id).values())
    channel_net = sum(
        int(r["gmv"] or 0) for r in store.channel_totals(run_id, model)) - alloc_refund
    net_balanced = (order_gmv - order_refund) == channel_net
    # 3) 渠道花费账：历史花费不被退款改写，仍与运行快照一致
    spend_snapshot = sum(int(item.get("cost_cents", 0)) for item in spend.values())
    spend_live = sum(int(r["cost_cents"] or 0)
                     for r in store.touchpoints(run_id) if r["status"] in {"COUNTED", "LATE"})
    spend_balanced = spend_snapshot == spend_live
    # 4) 预算余额账：最新已审批版本分完即余额 0，退款不动它
    latest = store.latest_budget(run_id)
    if latest:
        allocated = sum(int(row["spend_new_cents"])
                        for row in latest["payload"].get("rows", []))
        budget_balance = int(latest["total_cents"]) - allocated
        budget_version = int(latest["plan_version"])
    else:
        budget_balance = 0
        budget_version = None
    all_balanced = refund_balanced and net_balanced and spend_balanced and budget_balance == 0
    return {
        "refund_balanced": refund_balanced,
        "refund_row_cents": row_refund, "refund_alloc_cents": alloc_refund,
        "net_balanced": net_balanced,
        "order_gmv_cents": order_gmv, "order_refund_cents": order_refund,
        "channel_net_cents": channel_net,
        "spend_balanced": spend_balanced,
        "spend_snapshot_cents": spend_snapshot, "spend_live_cents": spend_live,
        "budget_balanced": budget_balance == 0,
        "budget_balance_cents": budget_balance, "budget_version": budget_version,
        "all_balanced": all_balanced,
    }
