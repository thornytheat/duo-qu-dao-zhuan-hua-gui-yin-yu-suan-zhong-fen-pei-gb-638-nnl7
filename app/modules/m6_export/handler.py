"""M6 入口：CSV / JSON / 清单下载。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..m4_attribution import models as m4_models
from . import service

router = APIRouter(prefix="/api", tags=["m6-export"])


@router.get("/export.csv")
def get_csv(request: Request) -> Response:
    store = request.app.state.store
    kind = request.query_params.get("kind", "touch")
    model = request.query_params.get("model", "time_decay")
    result = service.export_csv(store, request.query_params.get("run_id"), kind, model)
    return Response(content=result["body"], media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{result["filename"]}"'})


@router.get("/export.json")
def get_json(request: Request) -> dict:
    return service.export_json(request.app.state.store,
                               request.query_params.get("run_id"))


@router.get("/manifest")
def get_manifest(request: Request) -> dict:
    return service.manifest(request.app.state.store,
                            request.query_params.get("run_id"),
                            request.app.state.settings)


@router.get("/models")
def list_models() -> dict:
    return {"models": [{"code": code, "label": m4_models.label(code)}
                       for code in m4_models.MODEL_CODES]}
