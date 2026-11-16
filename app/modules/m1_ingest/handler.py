"""M1 入口：触发一次运行、查询运行与日志。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ... import errors
from . import service

router = APIRouter(prefix="/api", tags=["m1-ingest"])


class ImportRequest(BaseModel):
    dataset: str = "dirty"


@router.post("/import")
def post_import(request: Request, body: ImportRequest | None = None) -> dict:
    dataset = (body.dataset if body else "dirty") or "dirty"
    result = service.run(request.app.state.settings, request.app.state.store, dataset)
    stats = result["stats"]
    return {
        "run_id": result["run_id"],
        "status": result["status"],
        "repeat": result["repeat"],
        "accounts": stats["accounts"],
        "summary": stats["summary"],
        "reconcile": stats["reconcile"],
        "reason_distribution": stats["reason_distribution"],
    }


@router.get("/runs")
def list_runs(request: Request) -> dict:
    store = request.app.state.store
    limit = int(request.query_params.get("limit", 20))
    return {"runs": store.list_runs(limit)}


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> dict:
    run = request.app.state.store.get_run(run_id)
    if run is None:
        raise errors.NotFoundRun(f"运行不存在：{run_id}")
    return run


@router.get("/runs/{run_id}/log")
def get_log(request: Request, run_id: str) -> dict:
    store = request.app.state.store
    if store.get_run(run_id) is None:
        raise errors.NotFoundRun(f"运行不存在：{run_id}")
    after = int(request.query_params.get("after", 0))
    return {"run_id": run_id, "last_seq": store.log_count(run_id),
            "lines": store.logs(run_id, after)}
