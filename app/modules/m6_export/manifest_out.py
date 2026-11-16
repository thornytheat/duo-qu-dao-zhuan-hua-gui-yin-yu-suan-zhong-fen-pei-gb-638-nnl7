"""运行清单导出：把 run_id 用当前输入重新算一遍，能对上才算可复算。"""
from __future__ import annotations

from ... import manifest as manifest_mod


def build(run: dict, settings) -> dict:
    stored = run["manifest"]
    recomputed = manifest_mod.compute_run_id(
        stored.get("inputs", {}), stored.get("rule_version", settings.rule_version),
        stored.get("params", {}))
    return {
        "run_id": run["run_id"],
        "recomputed_run_id": recomputed,
        "replayable": recomputed == run["run_id"],
        "dataset": run["dataset"],
        "rule_version": run["rule_version"],
        "inputs": stored.get("inputs", {}),
        "params": stored.get("params", {}),
        "started_at": stored.get("started_at"),
        "finished_at": stored.get("finished_at"),
        "elapsed_ms": stored.get("elapsed_ms"),
        "tz": stored.get("tz", "+08:00"),
        "created_at": run["created_at"],
        "status": run["status"],
    }
