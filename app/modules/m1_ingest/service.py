"""M1 编排：读文件 → 归一 → 质量 → 窗口 → 归因 → 落库。

一次运行的全部证据（触点、订单、隔离、份额、日志）都挂在同一个 run_id 下。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ... import errors, manifest
from ..m2_window import service as m2_service
from ..m3_quality import service as m3_service
from ..m4_attribution import service as m4_service
from ..m5_budgets import service as m5_service
from . import decode, reader, sniff

FILES = {"touch": "touchpoints.csv", "order": "orders.csv", "alias": "aliases.csv"}
DATASETS = ("dirty", "clean", "header_only", "blank", "empty")

STATUS_LABELS = {
    "ok": "可解析",
    "empty": "文件为空",
    "header_only": "仅有表头",
    "blank_rows": "全部为空行",
    "truncated": "文件截断",
    "missing": "文件缺失",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def dataset_paths(settings, dataset: str) -> dict:
    if dataset not in DATASETS:
        raise errors.DatasetUnavailable(f"未知数据集：{dataset}", {"allowed": list(DATASETS)})
    base = Path(settings.sample_dir) / dataset
    if not base.is_dir():
        raise errors.DatasetUnavailable(f"样例目录不存在：{base}")
    return {kind: base / name for kind, name in FILES.items()}


def load(settings, dataset: str) -> dict:
    paths = dataset_paths(settings, dataset)
    plan_path = m5_service.constraints_path(settings)
    named = {path.name: path for path in paths.values()}
    named["budget_plan.json"] = plan_path
    digests = manifest.input_digests(named)
    frames, files = {}, {}

    for kind, path in paths.items():
        _, expected = sniff.spec(kind)
        if not path.exists():
            frames[kind] = {"header": [], "rows": []}
            files[kind] = {"file": path.name, "encoding": "—", "bytes": 0, "bom": False,
                           "rows": 0, "blanks": 0, "dup_headers": 0, "truncated": 0,
                           "mismatch": 0, "columns": 0, "status": "missing"}
            continue
        decoded = decode.read_text(path)
        parsed = reader.read_all(decoded["text"], expected)
        rows = [{"row_no": row.row_no, "raw_line": row.raw_line, "kind": row.kind,
                 "fields": row.fields, "notes": row.notes} for row in parsed["rows"]]
        data_rows = [row for row in rows if row["kind"] not in {"blank", "dup_header"}]
        status = "ok"
        if not parsed["header"] and not rows:
            status = "empty"
        elif parsed["header"] and not rows:
            status = "header_only"
        elif parsed["header"] and rows and not data_rows:
            status = "blank_rows"
        elif any(row["kind"] == "truncated" for row in rows):
            status = "truncated"
        files[kind] = {
            "file": path.name,
            "encoding": decoded["encoding"],
            "bytes": decoded["bytes"],
            "bom": decoded["bom"],
            "rows": len(data_rows),
            "blanks": sum(1 for row in rows if row["kind"] == "blank"),
            "dup_headers": sum(1 for row in rows if row["kind"] == "dup_header"),
            "truncated": sum(1 for row in rows if row["kind"] == "truncated"),
            "mismatch": sum(1 for row in rows if row["kind"] == "mismatch"),
            "columns": len(parsed["header"]),
            "status": status,
        }
        frames[kind] = {"header": parsed["header"], "rows": rows}

    return {
        "dataset": dataset,
        "frames": frames,
        "files": files,
        "digests": digests,
        "plan_digest": digests.get("budget_plan.json", {}).get("sha256"),
        "status": _dataset_status(files),
    }


def _dataset_status(files: dict) -> str:
    """只有三种空态会挡住整轮运行；截断只是文件级警告，半行单独隔离。"""
    for kind in ("touch", "order"):
        status = files.get(kind, {}).get("status")
        if status in ("empty", "header_only", "blank_rows"):
            return status
    return "ok"


def _empty_stats(loaded: dict, status: str) -> dict:
    accounts = {
        "touch_input": 0, "touch_counted": 0, "touch_late": 0, "touch_dropped": 0,
        "touch_out_of_window": 0, "touch_quarantined": 0, "order_input": 0,
        "order_ok": 0, "order_quarantined": 0, "alias_quarantined": 0,
        "balanced_touch": True, "balanced_order": True,
    }
    return {
        "status": status,
        "files": loaded["files"],
        "accounts": accounts,
        "summary": {"pass_rate_pct": 0.0, "quarantine_rate_pct": 0.0, "gmv_cents": 0,
                    "orders_ok": 0, "touches_kept": 0, "touches_dropped": 0,
                    "avg_gmv_cents": 0},
        "gmv_cents": 0,
        "spend": {},
        "cost_cents_total": 0,
        "models": [],
        "model_totals": {},
        "reason_distribution": [],
        "reconcile": {"touch_balanced": True, "order_balanced": True,
                      "gmv_balanced": True, "touch_delta": 0},
        "alias": {"classes": 0, "members": 0, "two_way": 0, "self_refs": 0},
        "channels": [],
    }


def _compose_stats(loaded: dict, normalized: dict, evaluated: dict,
                   attribution: dict) -> dict:
    accounts = evaluated["accounts"]
    summary = evaluated["summary"]
    spend = {code: {"label": item["label"], "cost_cents": int(item["cost_cents"]),
                    "n": int(item["n"])} for code, item in evaluated["spend"].items()}
    alias = normalized["alias"]
    return {
        "status": "done",
        "files": loaded["files"],
        "accounts": accounts,
        "summary": summary,
        "gmv_cents": summary["gmv_cents"],
        "spend": spend,
        "cost_cents_total": sum(item["cost_cents"] for item in spend.values()),
        "models": attribution["models"],
        "model_totals": attribution["model_totals"],
        "reason_distribution": evaluated["reason_distribution"],
        "reconcile": {
            "touch_balanced": accounts["balanced_touch"],
            "order_balanced": accounts["balanced_order"],
            "gmv_balanced": all(item["balanced"] for item in attribution["models"]),
            "touch_delta": accounts["touch_input"] - (
                accounts["touch_counted"] + accounts["touch_late"]
                + accounts["touch_dropped"]),
        },
        "alias": {"classes": len(alias["classes"]), "members": alias["resolved_count"],
                  "two_way": len(alias["two_way"]), "self_refs": len(alias["self_refs"])},
        "channels": evaluated["channels"],
    }


def run(settings, store, dataset: str = "dirty") -> dict:
    started = datetime.now(timezone.utc)
    loaded = load(settings, dataset)
    params = {
        "dataset": dataset,
        "click_window_days": settings.click_window.days,
        "impression_window_days": settings.impression_window.days,
        "decay_half_life_days": str(settings.decay_half_life_days),
        "budget_plan_sha256": loaded["plan_digest"],
    }
    run_id = manifest.compute_run_id(loaded["digests"], settings.rule_version, params)
    existing = store.get_run(run_id)
    logs: list[dict] = []

    def log(level: str, message: str) -> None:
        logs.append({"seq": len(logs) + 1, "ts": _now(), "level": level,
                     "message": message})

    files = loaded["files"]
    log("INFO", f"{dataset} · touchpoints.csv {files['touch']['bytes']} B · "
                f"{files['touch']['encoding']}" + (" · BOM" if files["touch"]["bom"] else ""))
    log("INFO", f"orders.csv {files['order']['bytes']} B · {files['order']['encoding']}")
    if files["alias"]["status"] == "missing":
        log("WARN", "别名文件缺失，账号不合并")
    trunc = files["touch"]["truncated"] + files["order"]["truncated"]
    if trunc:
        log("WARN", f"末行截断 {trunc} 行，已单独隔离")

    status = loaded["status"]
    if status != "ok":
        log("ERROR", f"数据集不可用：{STATUS_LABELS.get(status, status)}")
        stats = _empty_stats(loaded, status)
        finished = datetime.now(timezone.utc)
        record = {
            "run_id": run_id, "dataset": dataset,
            "rule_version": settings.rule_version, "created_at": _now(),
            "status": status, "stats": stats,
            "manifest": manifest.build(settings, dataset, loaded["digests"], params,
                                       run_id, started, finished),
        }
        store.replace_evidence(run_id, [], [], [], [], logs)
        store.save_run(record)
        return {"run_id": run_id, "status": status, "repeat": bool(existing),
                "stats": stats, "logs": logs}

    normalized = m2_service.normalize(settings, loaded["frames"])
    alias = normalized["alias"]
    log("INFO", f"别名：等价类 {len(alias['classes'])} 组 · 标识 {alias['resolved_count']} 个 · "
                f"双向引用 {len(alias['two_way'])} 对")

    evaluated = m3_service.evaluate(settings, normalized)
    accounts = evaluated["accounts"]
    log("INFO", f"质量：隔离 {len(evaluated['quarantined'])} 行 · "
                f"原因码 {len(evaluated['reason_distribution'])} 类")
    log("INFO", f"窗口：计入 {accounts['touch_counted']} · 迟到 {accounts['touch_late']} · "
                f"不计入 {accounts['touch_dropped']}")

    attribution = m4_service.attribute(settings, evaluated["ok_orders"],
                                       evaluated["kept_touches"],
                                       evaluated["matches"], evaluated["spend"])
    stats = _compose_stats(loaded, normalized, evaluated, attribution)
    broken = [item for item in stats["models"] if not item["balanced"]]
    if broken:
        log("ERROR", f"模型合计与订单成交额不一致：{broken[0]['label']}")
    else:
        log("INFO", f"归因：5 个模型合计均等于订单成交额 {stats['gmv_cents']} 分")

    log("INFO", f"对账：触点 {accounts['touch_input']} = {accounts['touch_counted']} + "
                f"{accounts['touch_late']} + {accounts['touch_dropped']} · "
                f"差额 {stats['reconcile']['touch_delta']}")
    finished = datetime.now(timezone.utc)
    log("INFO", f"完成 {run_id} · {int((finished - started).total_seconds() * 1000)} ms")

    store.replace_evidence(run_id, evaluated["touches"], evaluated["orders"],
                           evaluated["quarantined"], attribution["credits"], logs)
    record = {
        "run_id": run_id, "dataset": dataset, "rule_version": settings.rule_version,
        "created_at": _now(), "status": "done", "stats": stats,
        "manifest": manifest.build(settings, dataset, loaded["digests"], params,
                                   run_id, started, finished),
    }
    store.save_run(record)
    return {"run_id": run_id, "status": "done", "repeat": bool(existing),
            "stats": stats, "logs": logs}
