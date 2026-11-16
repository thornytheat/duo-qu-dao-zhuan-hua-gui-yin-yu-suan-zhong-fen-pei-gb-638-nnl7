"""M4 入口：归因结果（按模型）与逐单明细。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ... import errors
from . import models, service

router = APIRouter(prefix="/api", tags=["m4-attribution"])


@router.get("/attribution")
def get_attribution(request: Request) -> dict:
    store = request.app.state.store
    run_id = request.query_params.get("run_id")
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        raise errors.NotFoundRun("还没有运行记录")
    model = request.query_params.get("model") or "time_decay"
    if model not in models.MODEL_CODES:
        raise errors.BadRequest(
            f"未知归因模型：{model}", {"allowed": list(models.MODEL_CODES)})
    payload = service.aggregates(store, run["run_id"], model)
    return payload
