"""运行清单：run_id、输入摘要、规则版本、参数、耗时。

run_id 由输入内容与参数共同决定，同一份输入重跑得到同一个 run_id。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_digests(paths: dict[str, Path]) -> dict[str, dict]:
    result = {}
    for name, path in sorted(paths.items()):
        path = Path(path)
        if path.exists():
            result[name] = {"sha256": file_digest(path), "bytes": path.stat().st_size}
        else:
            result[name] = {"sha256": None, "bytes": None}
    return result


def compute_run_id(digests: dict[str, dict], rule_version: str, params: dict) -> str:
    material = {
        "inputs": {name: info["sha256"] for name, info in sorted(digests.items())},
        "rule_version": rule_version,
        "params": params,
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "run-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build(settings, dataset: str, digests: dict[str, dict], params: dict,
          run_id: str, started: datetime, finished: datetime) -> dict:
    return {
        "run_id": run_id,
        "dataset": dataset,
        "rule_version": settings.rule_version,
        "params": params,
        "inputs": digests,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "elapsed_ms": int((finished - started).total_seconds() * 1000),
        "tz": "+08:00",
    }
