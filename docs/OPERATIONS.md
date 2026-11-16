# 运行手册

## 启动

```bash
docker compose up -d --build
open http://127.0.0.1:18207/
```

首次启动库是空的，页面上点「运行样例」或执行：

```bash
curl -s -X POST http://127.0.0.1:18207/api/import \
  -H 'Content-Type: application/json' -d '{"dataset":"dirty"}'
```

## 数据集

| 名称 | 用途 |
|---|---|
| `dirty` | 脏样例，含 9 类问题，演示隔离与原因码 |
| `clean` | 干净样例，验证同一条代码路径 |
| `header_only` | 仅表头 |
| `blank` | 全空行 |
| `empty` | 0 字节文件 |

## 重跑

同一份输入重复导入会命中同一个 `run_id`，接口返回 `repeat=true`，行数不变。
换数据集或改规则版本才会产生新的 `run_id`。

## 重置

```bash
docker compose down -v          # 删除 SQLite 命名卷
docker compose up -d --build
```

## 日志

页面「作业总览」右侧是运行日志尾，每行带时间戳与级别，可暂停滚动；
接口为 `GET /api/runs/{run_id}/log?after=<seq>`。

## 排查顺序

1. 「触点与订单」看状态与原因码，确认丢弃的是哪一行、物理行号多少。
2. 「隔离区」看原因码分布，导出 CSV 交给投放同学对照原始导出。
3. 「归因结果」看五个模型合计是否都等于订单成交额。
4. 「置信区间」看份额区间与结论：半宽过大或首位不稳说明订单簇太少，补数据再算。
5. 「导出」看运行清单的 `recomputed_run_id` 是否与 `run_id` 一致。

## 退款结转

退款晚到时不需要重跑归因，历史花费与已审批预算版本都不会被改写：

```bash
# 提交批次（同 batch_id 重试幂等，中断后再调一次即续跑）
curl -s -X POST http://127.0.0.1:18207/api/refunds/batches \
  -H 'Content-Type: application/json' \
  -d '{"batch_id":"RF-202611-01","source":"refunds_ok.csv"}'
# 看净额、调整 ROI 与四账对平
curl -s 'http://127.0.0.1:18207/api/refunds/reconcile?model=time_decay'
# 下一版预算按净额 ROI 重排（旧版本保持 original_gmv 不变）
curl -s -X POST http://127.0.0.1:18207/api/budgets \
  -H 'Content-Type: application/json' \
  -d '{"plan_version":2,"total_yuan":60000,"use_refund_net":true}'
```

- 批次状态 `RUNNING / APPLIED / PARTIAL / FAILED`，失败只回滚本批，旧版保留，用同一批次号同内容重试即可。
- 同批次号换内容返回 409 `E_REFUND_CONFLICT`；超额退款返回隔离行 `RF_OVER_REFUND`，不冲减。
- 「退款结转」页可逐行查看隔离原因，并导出冲减份额 CSV。
