"""页面渲染：模板过滤器、导航、当前运行选择。

页面只读 store 的成品数据，不做归因或预算计算。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCAL_TZ = timezone(timedelta(hours=8))

NAV = (
    ("/", "作业总览", "overview"),
    ("/touchpoints", "触点与订单", "touchpoints"),
    ("/quarantine", "隔离区", "quarantine"),
    ("/attribution", "归因结果", "attribution"),
    ("/confidence", "置信区间", "confidence"),
    ("/budgets", "预算与批次", "budgets"),
    ("/export", "导出", "export"),
)

STATUS_LABELS = {
    "COUNTED": "计入", "LATE": "迟到", "DROPPED": "不计入",
    "OK": "有效", "QUARANTINED": "隔离",
    "done": "完成", "ready": "就绪", "failed": "失败",
}


@dataclass
class Ctx:
    settings: object
    store: object

    def current_run(self, run_id: str | None = None) -> dict | None:
        if run_id:
            return self.store.get_run(run_id)
        return self.store.latest_run()


def fmt_money(cents) -> str:
    if cents is None:
        return "—"
    value = Decimal(int(cents)) / Decimal(100)
    return f"{value:,.2f}"


def fmt_yuan(cents) -> str:
    if cents is None:
        return "—"
    return f"{Decimal(int(cents)) / Decimal(100):.0f}"


def fmt_int(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def fmt_pct(value) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):.1f}%"


def fmt_ratio(value) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):.2f}"


def parse_iso(text: str | None):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_local(text: str | None) -> str:
    moment = parse_iso(text)
    if moment is None:
        return "—"
    return moment.astimezone(LOCAL_TZ).strftime("%m-%d %H:%M:%S")


def fmt_local_full(text: str | None) -> str:
    moment = parse_iso(text)
    if moment is None:
        return "—"
    return moment.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def setup(app) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = templates.env
    env.filters["money"] = fmt_money
    env.filters["yuan"] = fmt_yuan
    env.filters["intcomma"] = fmt_int
    env.filters["pct"] = fmt_pct
    env.filters["ratio"] = fmt_ratio
    env.filters["local"] = fmt_local
    env.filters["localfull"] = fmt_local_full
    env.filters["reason"] = reasons.label
    env.filters["rlevel"] = reasons.level
    env.filters["status_label"] = lambda s: STATUS_LABELS.get(s, s or "—")
    env.globals["NAV"] = NAV
    app.state.templates = templates
    app.state.format_money = fmt_money


def render(request: Request, template: str, **context):
    ctx: Ctx = request.app.state.ctx
    run = context.pop("run", None)
    if run is None:
        run = ctx.current_run(request.query_params.get("run_id"))
    payload = {
        "run": run,
        "nav": NAV,
        "rule_version": ctx.settings.rule_version,
        "active": context.pop("active", ""),
    }
    payload.update(context)
    return request.app.state.templates.TemplateResponse(request, template, payload)
