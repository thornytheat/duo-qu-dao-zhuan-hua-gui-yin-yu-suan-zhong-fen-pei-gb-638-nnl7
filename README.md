# mixcredit 多渠道转化归因与预算重分配

六个投放渠道的触点与订单进系统，按窗口归因到订单，五个模型各自给出渠道份额，
再按边际 ROI 把预算重排到各渠道；最后按订单簇重采样，给出份额与 ROI 的区间和结论。
脏样例自带 9 类问题，导入后能逐行看到为什么不计数。晚到的退款按原订单触点份额冲减，
历史花费与已审批预算版本原样保留，退款差异单独结转，只影响下一版预算建议。

## 功能模块

| 模块 | 动作 | 入口 |
|---|---|---|
| M1 触点接入 | 导入触点/订单/别名，逐行诊断 | `POST /api/import` |
| M2 窗口与别名 | 字段契约、窗口定义、跨设备合并 | `GET /api/contract` |
| M3 质量隔离 | 原因码分布、隔离行、四账 | `GET /api/quarantine` |
| M4 归因计算 | 首次/末次/线性/时间衰减/位置加权 | `GET /api/attribution` |
| M5 预算重分配与批次 | 边际 ROI 贪心、渠道上下限、版本锁 | `POST /api/budgets` |
| M6 报告导出 | 证据行 CSV、整体 JSON、运行清单 | `GET /api/export.csv` |
| M7 归因置信区间 | 订单簇重采样、份额与 ROI 区间、稳健性结论 | `POST /api/confidence` |
| M8 退款结转 | 退款批次幂等续跑、按原单份额冲减、净额与调整 ROI、四账对平 | `POST /api/refunds/batches` |

## 核心规则

1. 触点按 `[t-窗口, t)` 归因到订单：点击 7 天、曝光 1 天，左端计入、右端不计入。
2. 时间先归一 UTC；`Z` 与 `+08:00` 两种写法落同一个窗口。
3. 入库晚于下单的触点仍计入，标记迟到。
4. 金额一律整数分，五个模型的渠道份额各自合计等于订单成交额（最大余额法拆分）。
5. 预算按边际 ROI 贪心加档，每次 1000 元，受渠道上下限约束，分完等于总额。
6. 方案版本必须等于当前最大版本 + 1，旧版本提交返回 409。
7. 区间按订单簇有放回抽样 200 次，同种子可复算；份额区间与 ROI 区间用 90% 分位，首位保持低于 90%、半宽超过 5 个百分点或前两渠道区间重叠都判「不稳」。
8. 退款不改写历史：按原订单在各模型下的触点份额（整数分、最大余额法）冲减，逐单守恒；同批次重试幂等不重复冲减，中断从行级检查点续跑，失败保留旧版并可同内容重试。
9. 退款差异单独结转：渠道净额 = 成交额 − 退款，调整 ROI 只在 `use_refund_net=true` 的下一版预算生效；退款、订单净额、渠道花费、预算余额四账对平。

详见 `docs/DEPTH.md`（功能模块表 + 陷阱表）与 `docs/DATA-DICTIONARY.md`（字段、单位、窗口、原因码）。

## 启动

```bash
docker compose up -d --build
```

打开 http://127.0.0.1:18207/ ，点「运行样例」，或：

```bash
curl -s -X POST http://127.0.0.1:18207/api/import \
  -H 'Content-Type: application/json' -d '{"dataset":"dirty"}'
```

本地跑测试：

```bash
pip install -r requirements.txt
python3 -m pytest -q
```

## 页面

| 路径 | 内容 |
|---|---|
| `/` | 作业总览：状态灯、四账、模型合计、原因码、运行日志尾 |
| `/touchpoints` | 触点与订单逐行状态 |
| `/quarantine` | 原因码分布与隔离明细 |
| `/attribution` | 五个模型的渠道份额与 ROI |
| `/confidence` | 份额与 ROI 区间、抽样参数、稳健性结论、未入帧订单 |
| `/budgets` | 渠道上下限、预算重排、批次历史 |
| `/refunds` | 退款批次、结转净额、调整 ROI、四账对平 |
| `/export` | 运行清单、输入摘要、下载 |

## 目录

```text
app/
  main.py config.py errors.py reasons.py manifest.py store.py web.py
  modules/m1_ingest/  m2_window/  m3_quality/  m4_attribution/  m5_budgets/  m6_export/
          m7_confidence/  m8_refunds/
  templates/  static/
docs/       DEPTH.md DATA-DICTIONARY.md OPERATIONS.md API.md SAMPLES.md
tests/      conftest.py test_<模块>.py test_e2e.py fixtures/
sample_data/ dirty/ clean/ header_only/ blank/ empty/ refunds/ budget_plan.json
```

## 已知不覆盖

退款只做金额冲减与净额结转，不自动发起反向投放、不回退已发生的媒体花费；不做增量实验
（对照组）；各渠道共用同一套窗口；预算衰减系数是常量；重采样不按用户或时段分层，也不做偏差校正。
