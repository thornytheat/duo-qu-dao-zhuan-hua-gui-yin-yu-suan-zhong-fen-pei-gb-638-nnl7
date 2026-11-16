"""M8 入口：退款批次提交/续跑/查询、结转净额视图、份额导出。"""
from __future__ import annotations

import io

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ... import errors
from ..m2_window.contract import CHANNEL_LABELS
from ..m4_attribution import models as m4_models
from . import service

router = APIRouter(prefix="/api/refunds", tags=["m8-refunds"])


class RefundBatchRequest(BaseModel):
    run_id: str | None = None
    batch_id: str | None = None
    source: str | None = None        # sample_data/refunds 下的文件名
    content: str | None = None       # 或直接给 CSV 文本


def _run_id(request: Request, run_id: str | None) -> str:
    run = request.app.state.store.get_run(run_id) if run_id else \
        request.app.state.store.latest_run()
    if run is None:
        raise errors.NotFoundRun("还没有运行记录")
    return run["run_id"]


@router.post("/batches")
def post_batch(request: Request, body: RefundBatchRequest) -> dict:
    store = request.app.state.store
    run_id = _run_id(request, body.run_id)
    if not body.batch_id or not body.batch_id.strip():
        raise errors.BadRequest("退款批次号不能为空")
    if not body.source and not body.content:
        raise errors.BadRequest("必须提供 source 文件名或 content CSV 文本")
    return service.process_batch(request.app.state.settings, store, run_id,
                                 body.batch_id.strip(), body.source or "", body.content)


@router.post("/batches/{batch_id}/resume")
def post_resume(request: Request, batch_id: str, body: RefundBatchRequest | None = None) -> dict:
    """断点续跑：用同一批次内容重新提交，已冲减行跳过，从断点继续。"""
    store = request.app.state.store
    run_id = _run_id(request, body.run_id if body else None)
    source = body.source if body else None
    content = body.content if body else None
    if not source and not content:
        existing = store.get_refund_batch(run_id, batch_id)
        if existing is None:
            raise errors.NotFoundRun(f"退款批次不存在：{batch_id}")
        source = existing.get("source")
    return service.process_batch(request.app.state.settings, store, run_id,
                                 batch_id, source or "", content)


@router.get("/batches")
def get_batches(request: Request) -> dict:
    store = request.app.state.store
    run_id = _run_id(request, request.query_params.get("run_id"))
    batches = store.list_refund_batches(run_id)
    return {"run_id": run_id, "count": len(batches), "batches": batches}


@router.get("/batches/{batch_id}")
def get_batch(request: Request, batch_id: str) -> dict:
    store = request.app.state.store
    run_id = _run_id(request, request.query_params.get("run_id"))
    batch = store.get_refund_batch(run_id, batch_id)
    if batch is None:
        raise errors.NotFoundRun(f"退款批次不存在：{batch_id}")
    return {"batch": batch, "rows": store.refund_rows(run_id, batch_id)}


@router.get("/carry-forward")
def get_carry_forward(request: Request) -> dict:
    store = request.app.state.store
    run_id = _run_id(request, request.query_params.get("run_id"))
    model = request.query_params.get("model") or "time_decay"
    return service.carry_forward(store, run_id, model)


@router.get("/reconcile")
def get_reconcile(request: Request) -> dict:
    store = request.app.state.store
    run_id = _run_id(request, request.query_params.get("run_id"))
    model = request.query_params.get("model") or "time_decay"
    if model not in m4_models.MODEL_CODES:
        raise errors.BadRequest(f"未知归因模型：{model}",
                                {"allowed": list(m4_models.MODEL_CODES)})
    return {"run_id": run_id, "model": model,
            "reconcile": service.reconcile(store, run_id, model)}


@router.get("/allocations.csv")
def get_allocations_csv(request: Request) -> Response:
    store = request.app.state.store
    run_id = _run_id(request, request.query_params.get("run_id"))
    model = request.query_params.get("model") or "time_decay"
    if model not in m4_models.MODEL_CODES:
        raise errors.BadRequest(f"未知归因模型：{model}",
                                {"allowed": list(m4_models.MODEL_CODES)})
    rows = store.refund_allocations(run_id, model)
    buffer = io.StringIO()
    buffer.write("退款批次,退款单号,归因模型,订单号,触点编号,渠道,渠道名称,冲减(分),位次\n")
    for row in rows:
        buffer.write(",".join([
            row["batch_id"], row["refund_id"], m4_models.label(row["model"]),
            row["order_id"], row["touch_id"], row["channel"],
            CHANNEL_LABELS.get(row["channel"], row["channel"]),
            str(row["cents"]), str(row["position"]),
        ]) + "\n")
    return Response(
        content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{run_id}-refund-{model}.csv"'})
