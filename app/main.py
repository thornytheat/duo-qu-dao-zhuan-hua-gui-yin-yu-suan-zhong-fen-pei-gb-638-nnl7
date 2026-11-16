"""应用装配：配置 → 存储 → 七个模块路由 → 七个页面。

用工厂函数而不是模块级实例，测试可以给每个用例一个临时库；
uvicorn 通过模块级 __getattr__ 在第一次访问时构造 app，导入不会建库。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from . import config, errors, reasons, web
from .modules.m1_ingest import handler as m1_handler
from .modules.m2_window import handler as m2_handler
from .modules.m2_window import contract as m2_contract
from .modules.m3_quality import handler as m3_handler
from .modules.m4_attribution import handler as m4_handler
from .modules.m4_attribution import models as m4_models
from .modules.m5_budgets import handler as m5_handler
from .modules.m5_budgets import batches as m5_batches
from .modules.m5_budgets import service as m5_service
from .modules.m6_export import handler as m6_handler
from .modules.m7_confidence import handler as m7_handler
from .modules.m7_confidence import service as m7_service
from .store import Store

MODULES = (m1_handler, m2_handler, m3_handler, m4_handler, m5_handler, m6_handler,
           m7_handler)


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.Settings.from_env()
    store = Store(settings.db_path)
    store.init()

    app = FastAPI(title="mixcredit 多渠道转化归因与预算重分配",
                  version=settings.rule_version)
    app.state.settings = settings
    app.state.store = store
    app.state.ctx = web.Ctx(settings=settings, store=store)
    web.setup(app)
    app.mount("/static", StaticFiles(directory=str(web.STATIC_DIR)), name="static")
    for module in MODULES:
        app.include_router(module.router)
    errors.install_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok", "rule_version": settings.rule_version,
                "runs": store.count_runs()}

    @app.get("/")
    def page_overview(request: Request):
        run = app.state.ctx.current_run()
        if run is None:
            return web.render(request, "index.html", active="overview",
                              accounts=None, logs=[], spend=[], models=[])
        stats = run["stats"]
        return web.render(
            request, "index.html", active="overview", run=run, stats=stats,
            accounts=stats["accounts"], summary=stats.get("summary", {}),
            files=stats.get("files", {}), reconcile=stats.get("reconcile", {}),
            models=stats.get("models", []),
            spend=sorted(stats.get("spend", {}).items(),
                         key=lambda item: -item[1]["cost_cents"]),
            reasons=sorted(stats.get("reason_distribution", []),
                           key=lambda item: -item["count"]),
            logs=store.logs(run["run_id"])[:120],
            channels=m2_contract.CHANNELS)

    @app.get("/touchpoints")
    def page_touchpoints(request: Request):
        run = app.state.ctx.current_run()
        if run is None:
            return web.render(request, "touchpoints.html", active="touchpoints",
                              touches=[], orders=[], counts={})
        status = request.query_params.get("status") or ""
        touches = store.touchpoints(run["run_id"], status or None)
        return web.render(request, "touchpoints.html", active="touchpoints",
                          run=run, accounts=run["stats"]["accounts"],
                          touches=touches[:250], orders=store.orders(run["run_id"]),
                          counts=store.touchpoint_counts(run["run_id"]),
                          status=status, total=len(touches),
                          channel_labels=m2_contract.CHANNEL_LABELS)

    @app.get("/quarantine")
    def page_quarantine(request: Request):
        run = app.state.ctx.current_run()
        if run is None:
            return web.render(request, "quarantine.html", active="quarantine",
                              rows=[], distribution=[])
        code = request.query_params.get("code") or ""
        rows = store.quarantine(run["run_id"], code or None)
        return web.render(request, "quarantine.html", active="quarantine", run=run,
                          rows=rows[:250], total=len(rows), code=code,
                          distribution=reasons.describe(),
                          counts=store.reason_counts(run["run_id"]))

    @app.get("/attribution")
    def page_attribution(request: Request):
        run = app.state.ctx.current_run()
        if run is None:
            return web.render(request, "attribution.html", active="attribution",
                              totals={}, channels=[], credits=[], model="time_decay")
        model = request.query_params.get("model") or "time_decay"
        if model not in m4_models.MODEL_CODES:
            model = "time_decay"
        channels = store.channel_totals(run["run_id"], model)
        spend = run["stats"].get("spend", {})
        model_total = int(store.model_totals(run["run_id"]).get(model, {}).get("gmv_cents", 0))
        rows = []
        for row in channels:
            cost = int(spend.get(row["channel"], {}).get("cost_cents", 0))
            rows.append({
                "channel": row["channel"], "gmv_cents": int(row["gmv"] or 0),
                "rows": row["n"], "late_rows": row["late_n"],
                "share_pct": (round(int(row["gmv"] or 0) * 100.0 / model_total, 2)
                              if model_total else 0.0),
                "spend_cents": cost,
                "roi": (round(int(row["gmv"] or 0) / cost, 2) if cost else None),
            })
        rows.sort(key=lambda item: (-item["gmv_cents"], item["channel"]))
        return web.render(
            request, "attribution.html", active="attribution", run=run,
            model=model, models=m4_models.MODELS,
            channels=rows, model_total=model_total,
            order_total=int(run["stats"].get("gmv_cents", 0)),
            totals=store.model_totals(run["run_id"]),
            credits=store.credits(run["run_id"], model)[:120],
            orders=store.orders(run["run_id"]),
            channel_labels=m2_contract.CHANNEL_LABELS)

    @app.get("/budgets")
    def page_budgets(request: Request):
        run = app.state.ctx.current_run()
        plan = m5_service.load_constraints(settings)
        if run is None:
            return web.render(request, "budgets.html", active="budgets",
                              plan=plan, budgets=[], batches=[], next_version=1)
        budgets = store.budgets(run["run_id"])
        return web.render(request, "budgets.html", active="budgets", run=run,
                          plan=plan, budgets=budgets, stats=run["stats"],
                          latest=budgets[0] if budgets else None,
                          next_version=store.next_plan_version(run["run_id"]),
                          batches=m5_batches.history(store, 12))

    @app.get("/confidence")
    def page_confidence(request: Request):
        run = app.state.ctx.current_run()
        query = request.query_params
        model = query.get("model") or "time_decay"
        if model not in m4_models.MODEL_CODES:
            model = "time_decay"
        form = {
            "model": model,
            "resamples": query.get("resamples") or str(m7_service.DEFAULT_RESAMPLES),
            "seed": query.get("seed") or str(m7_service.DEFAULT_SEED),
        }
        if run is None:
            return web.render(request, "confidence.html", active="confidence",
                              result=None, form=form, error=None,
                              models=m4_models.MODELS)
        error = None
        try:
            result = m7_service.compute(settings, store, run["run_id"], model,
                                        int(form["resamples"]), int(form["seed"]))
        except errors.DomainError as exc:
            result = m7_service.compute(settings, store, run["run_id"], model)
            error = exc.message
        except ValueError:
            result = m7_service.compute(settings, store, run["run_id"], model)
            error = "重采样次数与种子需为整数"
        return web.render(request, "confidence.html", active="confidence",
                          result=result, form=form, error=error,
                          models=m4_models.MODELS)

    @app.get("/export")
    def page_export(request: Request):
        run = app.state.ctx.current_run()
        if run is None:
            return web.render(request, "export.html", active="export",
                              manifest=None, counts={})
        return web.render(
            request, "export.html", active="export", run=run,
            manifest=m6_handler.service.manifest(store, run["run_id"], settings),
            counts=store.touchpoint_counts(run["run_id"]),
            models=m4_models.MODELS, stats=run["stats"])

    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """`uvicorn app.main:app` 用；导入本模块不会建库。"""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
