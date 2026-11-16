"""M5 入口：预算重分配、方案历史、批次列表与详情。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ... import errors
from ..m4_attribution import models as m4_models
from . import batches, service

router = APIRouter(prefix="/api", tags=["m5-budgets"])


class BudgetRequest(BaseModel):
    run_id: str | None = None
    plan_version: int = Field(ge=1)
    total_yuan: int = Field(gt=0, le=100_000_000)
    model: str = "time_decay"
    use_refund_net: bool = False


@router.post("/budgets")
def post_budget(request: Request, body: BudgetRequest) -> dict:
    store = request.app.state.store
    run = store.get_run(body.run_id) if body.run_id else store.latest_run()
    if run is None:
        raise errors.NotFoundRun("还没有运行记录")
    if body.model not in m4_models.MODEL_CODES:
        raise errors.BadRequest(f"未知归因模型：{body.model}",
                                {"allowed": list(m4_models.MODEL_CODES)})
    return service.reallocate(request.app.state.settings, store, run["run_id"],
                              body.plan_version, body.total_yuan * 100, body.model,
                              body.use_refund_net)


@router.get("/budgets")
def get_budgets(request: Request) -> dict:
    store = request.app.state.store
    run_id = request.query_params.get("run_id")
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        raise errors.NotFoundRun("还没有运行记录")
    rows = store.budgets(run["run_id"])
    return {"run_id": run["run_id"], "count": len(rows),
            "next_plan_version": store.next_plan_version(run["run_id"]),
            "plans": rows}


@router.get("/batches")
def get_batches(request: Request) -> dict:
    limit = int(request.query_params.get("limit", 30))
    return {"batches": batches.history(request.app.state.store, limit)}


@router.get("/batches/{run_id}")
def get_batch(request: Request, run_id: str) -> dict:
    return batches.detail(request.app.state.store, run_id)
