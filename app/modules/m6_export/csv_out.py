"""CSV 导出：触点评判、逐单份额、订单三张证据表。

每行都带物理行号与原因码，人工复核时不用回源码。
"""
from __future__ import annotations

import csv
import io

from ..m2_window.contract import CHANNEL_LABELS
from ..m4_attribution import models as m4_models

TOUCH_HEADER = ["触点编号", "物理行", "主账号", "渠道", "渠道名称", "事件类型",
                "事件时间(UTC)", "入库时间(UTC)", "成本(分)", "状态", "原因码", "迟到"]
CREDIT_HEADER = ["归因模型", "订单号", "触点编号", "渠道", "渠道名称", "份额(分)",
                 "触点位置", "迟到"]
ORDER_HEADER = ["订单号", "物理行", "主账号", "成交额(分)", "下单时间(UTC)", "状态", "原因码"]


def _money(cents) -> str:
    return f"{int(cents or 0)}"


def touchpoints_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TOUCH_HEADER)
    for row in rows:
        writer.writerow([
            row["touch_id"], row["raw_line"], row["canonical_user"], row["channel"],
            CHANNEL_LABELS.get(row["channel"], row["channel"]),
            row["event_type"], row["event_time_utc"], row["ingested_at_utc"],
            _money(row["cost_cents"]), row["status"], row["reason"] or "",
            "1" if row["late"] else "0",
        ])
    return buffer.getvalue()


def credits_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CREDIT_HEADER)
    for row in rows:
        writer.writerow([
            m4_models.label(row["model"]), row["order_id"], row["touch_id"],
            row["channel"], CHANNEL_LABELS.get(row["channel"], row["channel"]),
            _money(row["gmv_cents"]), row["position"], "1" if row["late"] else "0",
        ])
    return buffer.getvalue()


def orders_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ORDER_HEADER)
    for row in rows:
        writer.writerow([row["order_id"], row["raw_line"], row["canonical_user"],
                         _money(row["gmv_cents"]), row["order_time_utc"],
                         row["status"], row["reason"] or ""])
    return buffer.getvalue()


EXPORTERS = {
    "touch": ("touchpoints", touchpoints_csv),
    "credit": ("credits", credits_csv),
    "order": ("orders", orders_csv),
}
