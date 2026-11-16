"""原因码唯一来源：隔离、丢弃、文件级失败都从这里取码。

一行可能同时命中多条规则，取值规则：序号最小的码胜出，与规则书写顺序无关。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    code: str
    order: int
    label: str
    scope: str


_RAW = (
    ("TD_COL_COUNT", 10, "列数不齐", "touch"),
    ("TD_TIME_UNPARSABLE", 20, "事件时间无法解析", "touch"),
    ("TD_CHANNEL_UNKNOWN", 30, "渠道缺失或未知", "touch"),
    ("TD_EVENT_TYPE_UNKNOWN", 40, "事件类型未知", "touch"),
    ("TD_COST_INVALID", 50, "成本无法解析", "touch"),
    ("TD_TOUCH_DUP_CONFLICT", 60, "触点编号重复且字段冲突", "touch"),
    ("TD_TOUCH_DUP_IDENTICAL", 61, "触点编号重复且内容相同", "touch"),
    ("OD_TIME_UNPARSABLE", 70, "订单时间无法解析", "order"),
    ("OD_GMV_INVALID", 80, "订单金额无法解析", "order"),
    ("OD_DUP_CONFLICT", 90, "订单号重复且金额冲突", "order"),
    ("OD_DUP_IDENTICAL", 91, "订单号重复且内容相同", "order"),
    ("FILE_TRUNCATED", 100, "文件截断", "file"),
    ("FILE_EMPTY", 110, "文件为空", "file"),
    ("FILE_HEADER_ONLY", 120, "仅有表头", "file"),
    ("FILE_BLANK_ROWS", 130, "全部为空行", "file"),
    ("ALIAS_BAD_ROW", 140, "别名行不完整", "alias"),
    ("CI_DUP_ORDER_KEPT", 200, "同一订单号多行只算一次", "confidence"),
    ("CI_FRAME_EMPTY", 210, "没有可重采样的订单", "confidence"),
    ("CI_FRAME_THIN", 220, "订单簇少于 8 单", "confidence"),
    ("CI_WIDE_INTERVAL", 230, "份额区间半宽超过 5 个百分点", "confidence"),
    ("CI_UNSTABLE_RANK", 240, "首位渠道在重采样中不稳", "confidence"),
    ("CI_INTERVAL_OVERLAP", 250, "前两渠道区间重叠", "confidence"),
    ("CI_BUDGET_MISMATCH", 260, "预算首位与份额首位不一致", "confidence"),
    ("CI_SAME_SECOND_TIE", 270, "同秒触点按编号定序", "confidence"),
    ("OUT_OF_WINDOW", 900, "不在归因窗口内", "window"),
)

REASONS = tuple(Reason(*row) for row in _RAW)
BY_CODE = {reason.code: reason for reason in REASONS}
PRIORITY = {reason.code: reason.order for reason in REASONS}

# 隔离区里展示的码是数据侧问题，窗口外只是不计入
QUARANTINE_CODES = tuple(r.code for r in REASONS if r.scope in {"touch", "order", "alias"})

LEVELS = {"touch": "隔离", "order": "隔离", "alias": "隔离", "file": "失败",
          "window": "不计入", "confidence": "结论"}


def label(code: str) -> str:
    reason = BY_CODE.get(code)
    return reason.label if reason else code


def level(code: str) -> str:
    reason = BY_CODE.get(code)
    return LEVELS.get(reason.scope, "其他") if reason else "其他"


def pick(codes) -> str | None:
    """多条规则同时命中时，按固定优先级取一个码。"""
    candidates = [code for code in codes if code in PRIORITY]
    if not candidates:
        return None
    return min(candidates, key=lambda code: PRIORITY[code])


def describe() -> list[dict]:
    return [
        {"code": r.code, "label": r.label, "scope": r.scope, "priority": r.order}
        for r in REASONS
    ]
