"""M3 入口：隔离区查询与 CSV 导出。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ... import errors
from . import quarantine

router = APIRouter(prefix="/api", tags=["m3-quality"])


def _current(request: Request) -> dict:
    store = request.app.state.store
    run_id = request.query_params.get("run_id")
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        raise errors.NotFoundRun("还没有运行记录")
    return run


@router.get("/quarantine")
def get_quarantine(request: Request) -> dict:
    run = _current(request)
    code = request.query_params.get("code") or None
    rows = request.app.state.store.quarantine(run["run_id"], code)
    return {
        "run_id": run["run_id"],
        "count": len(rows),
        "rows": rows,
        "distribution": quarantine.distribution(
            request.app.state.store.quarantine(run["run_id"])),
        "accounts": run["stats"].get("accounts", {}),
    }


@router.get("/quarantine.csv")
def get_quarantine_csv(request: Request) -> Response:
    run = _current(request)
    rows = request.app.state.store.quarantine(run["run_id"])
    body = quarantine.export_csv(rows)
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="quarantine-{run["run_id"]}.csv"'})
