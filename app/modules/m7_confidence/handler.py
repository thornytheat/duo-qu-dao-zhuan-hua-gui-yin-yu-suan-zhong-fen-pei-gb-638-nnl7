"""M7 入口：重采样区间与稳健性结论。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ... import errors
from . import service

router = APIRouter(prefix="/api", tags=["m7-confidence"])


class ConfidenceRequest(BaseModel):
    run_id: str | None = None
    model: str = "time_decay"
    resamples: int = service.DEFAULT_RESAMPLES
    seed: int = service.DEFAULT_SEED
    alpha_pct: int = service.DEFAULT_ALPHA_PCT


def _run(store, run_id: str | None) -> dict:
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        raise errors.NotFoundRun("还没有运行记录")
    return run


@router.post("/confidence")
def post_confidence(request: Request, body: ConfidenceRequest) -> dict:
    store = request.app.state.store
    run = _run(store, body.run_id)
    return service.compute(request.app.state.settings, store, run["run_id"],
                           body.model, body.resamples, body.seed, body.alpha_pct)


@router.get("/confidence")
def get_confidence(request: Request) -> dict:
    store = request.app.state.store
    query = request.query_params
    run = _run(store, query.get("run_id"))
    try:
        resamples = int(query.get("resamples", service.DEFAULT_RESAMPLES))
        seed = int(query.get("seed", service.DEFAULT_SEED))
        alpha_pct = int(query.get("alpha_pct", service.DEFAULT_ALPHA_PCT))
    except (TypeError, ValueError):
        raise errors.BadRequest("重采样次数、种子、区间必须是整数")
    return service.compute(request.app.state.settings, store, run["run_id"],
                           query.get("model") or "time_decay",
                           resamples, seed, alpha_pct)
