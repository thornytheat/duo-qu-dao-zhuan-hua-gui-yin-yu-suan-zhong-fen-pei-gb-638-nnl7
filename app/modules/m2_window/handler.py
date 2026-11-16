"""M2 入口：字段契约与窗口定义。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import contract

router = APIRouter(prefix="/api", tags=["m2-window"])


@router.get("/contract")
def get_contract(request: Request) -> dict:
    settings = request.app.state.settings
    return contract.payload(settings)
