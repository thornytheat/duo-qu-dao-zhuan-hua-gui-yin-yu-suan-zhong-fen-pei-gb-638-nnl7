# 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/import` | 运行一次，body `{"dataset":"dirty"}` |
| GET | `/api/runs` | 运行列表 |
| GET | `/api/runs/{run_id}` | 运行详情（含四项账目） |
| GET | `/api/runs/{run_id}/log` | 日志，`after=<seq>` 增量拉取 |
| GET | `/api/contract` | 字段契约、窗口、模型、渠道 |
| GET | `/api/quarantine` | 隔离行，`code=` 过滤 |
| GET | `/api/quarantine.csv` | 隔离区 CSV |
| GET | `/api/attribution` | 归因结果，`model=` 五选一 |
| POST | `/api/budgets` | 预算重排，body `{plan_version,total_yuan,model}` |
| GET | `/api/budgets` | 方案历史与下一版号 |
| POST | `/api/confidence` | 重采样区间，body `{model,resamples,seed,alpha_pct}` |
| GET | `/api/confidence` | 同上，参数走查询串 |
| GET | `/api/batches` | 批次列表 |
| GET | `/api/batches/{run_id}` | 批次详情 |
| GET | `/api/export.csv` | 证据行 CSV，`kind=touch|order|credit` |
| GET | `/api/export.json` | 整体 JSON |
| GET | `/api/manifest` | 运行清单（含复算 run_id） |
| GET | `/healthz` | 存活检查 |

## 失败码

| HTTP | 错误码 | 场景 |
|---|---|---|
| 400 | `E_BAD_REQUEST` | 未知模型、未知导出类型、版本号跳跃、重采样次数越界 |
| 404 | `E_RUN_NOT_FOUND` | run_id 不存在 |
| 404 | `E_DATASET_NOT_FOUND` | 数据集不存在 |
| 409 | `E_STALE_PLAN_VERSION` | 旧方案版本提交 |
| 422 | `E_BUDGET_INFEASIBLE` | 总额低于下限之和或高于上限之和 |
