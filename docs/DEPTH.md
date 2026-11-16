# 深度登记 mixcredit

- 规模：中型（8 个功能模块，59 个 Python 文件，112 条测试）
- 核心规则：触点按 `[t-窗口, t)` 归因到订单，五个模型各自合计必须等于订单成交额；预算按边际 ROI 贪心加档，受渠道上下限约束且分完等于总额；置信区间按订单簇有放回重采样 200 次，给出渠道份额与 ROI 的 90% 区间并判定建议是否成立；晚到退款按原订单触点份额冲减，历史花费与已审批版本原样保留，退款差异单独结转，只影响下一版预算建议。
- 使用者与工作流：清野日化增长组 7 人，每周把六个渠道的触点与订单导入，先看触点是否计入、再看五个模型的渠道份额，最后按总额重排预算并把结果导出复核。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 触点接入 | 导入脏样例，看到逐行诊断与文件级状态 | POST /api/import | `app/modules/m1_ingest/service.py` `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/sniff.py` | `tests/test_ingest.py` |
| M2 窗口与别名 | 查字段契约、窗口定义，确认跨设备合并 | GET /api/contract | `app/modules/m2_window/contract.py` `app/modules/m2_window/coerce.py` `app/modules/m2_window/window.py` `app/modules/m2_window/alias.py` | `tests/test_window.py` |
| M3 质量隔离 | 看原因码分布、隔离行与四账 | GET /api/quarantine | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` `app/modules/m3_quality/service.py` | `tests/test_quality.py` |
| M4 归因计算 | 切换五个模型，看渠道份额与 ROI | GET /api/attribution | `app/modules/m4_attribution/models.py` `app/modules/m4_attribution/decay.py` `app/modules/m4_attribution/allocate.py` | `tests/test_attribution.py` |
| M5 预算重分配与批次 | 提交总额与方案版本，看新预算与版本历史 | POST /api/budgets | `app/modules/m5_budgets/greedy.py` `app/modules/m5_budgets/roi.py` `app/modules/m5_budgets/batches.py` | `tests/test_budgets.py` |
| M6 报告导出 | 导出触点/订单/份额 CSV、整体 JSON 与运行清单 | GET /api/export.csv | `app/modules/m6_export/csv_out.py` `app/modules/m6_export/json_out.py` `app/modules/m6_export/manifest_out.py` | `tests/test_export.py` |
| M7 归因置信区间 | 选模型与抽样次数，看渠道份额与 ROI 的区间和结论 | POST /api/confidence | `app/modules/m7_confidence/frame.py` `app/modules/m7_confidence/resample.py` `app/modules/m7_confidence/interval.py` `app/modules/m7_confidence/service.py` | `tests/test_m7_bootstrap.py` `tests/test_m7_stability.py` |
| M8 退款结转 | 提交退款批次，看净额、调整 ROI 与四账对平；失败可续跑 | POST /api/refunds/batches | `app/modules/m8_refunds/service.py` `app/modules/m8_refunds/allocate.py` `app/modules/m8_refunds/parse.py` | `tests/test_refunds.py` `tests/test_refunds_api.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 别名文件是 GB18030 中文表头 | `open()` 直接崩或列名乱码 | 编码回退链 utf-8-sig → utf-8 → gb18030，并把实际编码写进清单 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_dirty_files_use_bom_and_gb18030_fallback` |
| TRAP-02 | 触点文件带 UTF-8 BOM | 首列名变 `\ufefftouch_id`，后面按列名取值全空 | 用 utf-8-sig 解码并统一 strip BOM | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_first_column_name_is_clean` |
| TRAP-03 | 金额里有引号包住的逗号 | `splitlines()` 或按逗号切把一条记录拆成两条 | 用 csv 模块流式解析，保留物理行号 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_quoted_comma_keeps_one_record_per_line` |
| TRAP-04 | 分段导出把表头又写了一遍 | 重复表头被当成一条数据行 | 识别重复表头并单独计数，不进数据行 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_duplicate_header_and_blank_line_are_not_data_rows` |
| TRAP-05 | 行尾多了一个字段 | 静默补空或错列 | 严格列数校验，整行进隔离区带 `TD_COL_COUNT` | `app/modules/m2_window/service.py` | `tests/test_ingest.py::test_row_with_extra_column_is_quarantined` |
| TRAP-06 | 成本写成 `1,234.50` 或全角数字 | `Decimal()` 崩或截断成错值 | NFKC 归一 + 去千分位 + 分取整 | `app/modules/m2_window/coerce.py` | `tests/test_ingest.py::test_thousands_and_fullwidth_amounts_parse` |
| TRAP-08 | 触点编号带前导零 | `int()` 化后丢零，证据行对不上 | 编号全列按字符串保形 | `app/modules/m2_window/service.py` | `tests/test_ingest.py::test_leading_zero_touch_id_kept_as_string` |
| TRAP-10 | 份额用浮点累加 | 1000 行合计差几分 | 金额一律整数分，拆分用整数运算 | `app/modules/m4_attribution/allocate.py` | `tests/test_attribution.py::test_five_models_conserve_order_gmv` |
| TRAP-11 | 事件时间 `Z` 与 `+08:00` 混写 | 同一时刻落进两个窗口，重复或漏算 | 先归一 UTC 再判窗口，保留原始偏移 | `app/modules/m2_window/coerce.py` | `tests/test_window.py::test_two_timezone_forms_hit_same_instant` |
| TRAP-13 | 入库时间晚于下单时间 | 水位线一推进就丢掉迟到触点 | 迟到仍计入并打 `late` 标记，证据行可查 | `app/modules/m2_window/window.py` | `tests/test_quality.py::test_late_touchpoint_is_counted_and_flagged` |
| TRAP-14 | 同一触点编号渠道与成本冲突 | 后写覆盖，或整批丢弃 | 冲突组全部隔离；内容相同的只保留首条 | `app/modules/m3_quality/rules.py` | `tests/test_quality.py::test_duplicate_touch_id_conflict_quarantines_both` |
| TRAP-16 | 末行没有换行且字段被切断 | 半行被当成有效触点 | 尾行完整性校验，半行隔离并标记文件截断 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_truncated_tail_line_is_rejected` |
| TRAP-18 | 0 行 / 仅表头 / 全空行 | 一律返回"成功导入 0 行"，或除零 | 三种空态返回不同结论码 | `app/modules/m1_ingest/service.py` | `tests/test_ingest.py::test_three_empty_states_are_distinct` |
| TRAP-19 | 一行同时命中渠道未知与事件未知 | 结果取决于规则书写顺序 | 原因码表带固定序号，取最小序号 | `app/reasons.py` | `tests/test_quality.py::test_reason_priority_is_independent_of_rule_order` |
| TRAP-22 | 同一人有设备号、旧账号多个别名 | 合并顺序不同结果不同 | 并查集，代表元取等价类里字典序最小 | `app/modules/m2_window/alias.py` | `tests/test_window.py::test_alias_merge_is_order_independent` |
| TRAP-23 | 触点正好落在窗口端点 | 两端都算，重复计入 | 半开区间 `[start, end)` | `app/modules/m2_window/window.py` | `tests/test_window.py::test_window_end_is_exclusive` |
| TRAP-24 | 份额与预算各自四舍五入 | 分完合计不等于原额 | 整数分 + 最大余额法 | `app/modules/m5_budgets/greedy.py` | `tests/test_budgets.py::test_allocation_conserves_total` |
| TRAP-26 | 旧方案版本覆盖新方案 | 后提交无条件覆盖 | 版本号必须等于当前最大版本 + 1，旧版本 409 | `app/modules/m5_budgets/batches.py` | `tests/test_budgets.py::test_stale_plan_version_rejected` |
| TRAP-27 | 同一秒的两个触点排序 | 只按事件时间排序，次序跟着证据行序走，首触点与位置权重会翻转，渠道份额随之漂移 | 触点按 (`事件时间`, `触点编号`) 全序排序，帧摘要与区间对输入行序不变 | `app/modules/m7_confidence/frame.py` | `tests/test_m7_bootstrap.py::test_same_second_touches_use_touch_id_tie_break` |
| TRAP-28 | 同一订单号出现两行（冲突、重复提交、按份额行抽取） | 按行累加或按份额行重采样，一单被计两次，帧合计高于对账成交额 | 按订单号分组去重，只取 row_no 最小的有效行；抽样单位是订单簇，重复行计数并在页面列出 | `app/modules/m7_confidence/frame.py` | `tests/test_m7_bootstrap.py::test_frame_keeps_one_row_per_order_and_skips_invalid` |
| TRAP-30 | 晚到退款回来后直接重算历史 | 已发生的媒体花费与已审批预算版本被改写，审计链断裂 | 退款写独立三表，orders/credits/budgets/logs 一律不改；差异只通过结转视图影响下一版建议 | `app/modules/m8_refunds/service.py` | `tests/test_refunds.py::test_refund_does_not_rewrite_history` |
| TRAP-31 | 同一退款批次重试 | 重复冲减，退款额翻倍 | 批次内容指纹 + 行级 `applied` 检查点；终态批次幂等回放，RUNNING 从断点续跑，已冲减行跳过 | `app/modules/m8_refunds/service.py` | `tests/test_refunds.py::test_retry_same_batch_is_idempotent` `tests/test_refunds.py::test_resume_from_checkpoint` |
| TRAP-32 | 退款按渠道均摊或重新归因 | 退款份额与原单触点脱钩，被退款渠道继续吃预算 | 退款沿用该订单每个模型下的既有 credits 份额，整数分 + 最大余额法拆分，逐单守恒 | `app/modules/m8_refunds/allocate.py` | `tests/test_refunds.py::test_split_refund_follows_original_credit_share` |
| TRAP-33 | 批次处理到一半进程中断 | 部分行已冲减、部分未处理，重跑要么重复要么漏 | 行级检查点落库，份额在终态从全部 APPLIED 行统一重建；续跑得到完整且不重复的拆分 | `app/modules/m8_refunds/service.py` | `tests/test_refunds.py::test_resume_from_checkpoint` |
| TRAP-34 | 处理失败时半成品污染旧版 | 旧预算版本被覆盖、残留份额影响对账 | 失败标 `FAILED` 并清掉本批份额残骸，旧批次与已审批预算原样保留；同内容可重试 | `app/modules/m8_refunds/service.py` | `tests/test_refunds.py::test_failure_keeps_old_budgets_and_marks_batch_failed` |
| TRAP-35 | 累计退款超过订单成交额 | 净额被冲成负数，ROI 失真 | 跨批次汇总已退款额，超额行进隔离区带 `RF_OVER_REFUND`，不冲减 | `app/modules/m8_refunds/service.py` | `tests/test_refunds.py::test_over_refund_checked_across_batches` |
| TRAP-36 | 同批次号被塞进不同内容 | 后写覆盖，原冲减无法追溯 | 批次内容指纹不一致直接 409 `E_REFUND_CONFLICT`，旧批次保留 | `app/modules/m8_refunds/service.py` | `tests/test_refunds.py::test_same_batch_id_with_different_content_conflicts` |

## 反例 / 不变量 / 端到端

- 反例：`tests/test_attribution.py::test_naive_float_split_loses_a_cent` —— 逐份四舍五入合计只有 999 分，当前实现 1000 分。
- 反例（v2）：`tests/test_m7_bootstrap.py::test_duplicate_order_group_counts_once` —— 同一订单号两行按行累加是 20000 分，帧只算 10000 分，另一行进证据表但不进抽样。
- 不变量：`tests/test_e2e.py::test_rerun_is_idempotent` —— 同输入重跑命中同一批次键，行数与四账不变。
- 不变量（v2）：`tests/test_m7_stability.py::test_every_draw_conserves_cluster_total` —— 每次抽样的渠道合计 = 该次抽到的订单簇成交额合计，差额为 0。
- 端到端：`tests/test_e2e.py::test_budget_to_export_flow` —— 导入 → 预算 v1 → 导出 JSON → 清单复算。

## 对账规则

- 触点：输入行数 = 计入 + 迟到 + 不计入；不计入 = 窗口外 + 隔离。
- 订单：输入行数 = 有效 + 隔离。
- 金额：每个模型的渠道份额合计 = 有效订单成交额合计（整数分，差额必须为 0）。
- 预算：各渠道新预算合计 = 提交总额；每档加投后仍受渠道上下限约束。
- 置信区间：帧合计 = 有效订单成交额；每个订单簇的触点份额合计 = 该单成交额；每次抽样合计 = 抽到的订单簇成交额之和；区间下限 ≤ 点估计 ≤ 区间上限。
- 退款（四账对平）：① 退款批次行金额合计 = 各模型份额拆分合计；② 订单成交额 − 订单维度退款 = 渠道净额合计；③ 渠道花费运行快照 = 证据行汇总（退款不改写）；④ 最新已审批预算分完即余额 0。任一不平则 `all_balanced=false`。

## 已知不覆盖

- 归因窗口只按事件类型区分（点击 7 天、曝光 1 天），不做各渠道单独窗口。
- 不做增量实验（对照组/PSA），只有观察数据上的归因与预算重排。
- 退款只做金额冲减与净额结转，不自动发起反向投放，也不回退已发生的媒体花费；调整 ROI 仅作为下一版预算建议的输入。
- 时区只处理带偏移与固定 +08:00 的无偏移写法，不处理夏令时地区。
- 预算衰减系数是配置常量，不从历史数据估计。
- 重采样只在观察到的订单簇上做，不按用户或时段分层，也没做偏差校正（BCa）；订单少于 8 单时区间只作参考。
- 区间用固定种子的线性同余流，同种子可复算；换种子会得到略不同的上下限，属正常波动。
