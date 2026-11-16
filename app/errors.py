"""领域异常到 HTTP 的映射：handler 只抛语义异常，不自己拼状态码。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status = 400
    code = "E_BAD_REQUEST"

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class BadRequest(DomainError):
    status = 400
    code = "E_BAD_REQUEST"


class NotFoundRun(DomainError):
    status = 404
    code = "E_RUN_NOT_FOUND"


class DatasetUnavailable(DomainError):
    status = 404
    code = "E_DATASET_NOT_FOUND"


class StaleVersion(DomainError):
    status = 409
    code = "E_STALE_PLAN_VERSION"


class BudgetInfeasible(DomainError):
    status = 422
    code = "E_BUDGET_INFEASIBLE"


class RefundConflict(DomainError):
    status = 409
    code = "E_REFUND_CONFLICT"


class RefundInfeasible(DomainError):
    status = 422
    code = "E_REFUND_INFEASIBLE"


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError):  # noqa: ANN001
        body = {"error": exc.code, "message": exc.message}
        body.update(exc.detail)
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):  # noqa: ANN001
        return JSONResponse(status_code=400,
                            content={"error": "E_BAD_REQUEST", "message": str(exc)})
