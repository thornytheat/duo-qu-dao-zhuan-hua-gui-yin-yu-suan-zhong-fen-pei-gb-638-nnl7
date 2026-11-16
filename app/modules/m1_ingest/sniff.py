"""表头识别与列名演进映射：旧列名先映射到契约列名再取值。"""
from __future__ import annotations

TOUCH_COLUMNS = ("touch_id", "user_id", "device_id", "channel", "event_type",
                 "event_time", "ingested_at", "cost")
ORDER_COLUMNS = ("order_id", "user_id", "gmv", "order_time", "unit")
ALIAS_COLUMNS = ("alias_id", "canonical_id")

# 历史导出里出现过的列名，按同一含义映射
RENAMES = {
    "touch_id": ("touch_id", "tid", "触点编号"),
    "user_id": ("user_id", "uid", "用户"),
    "device_id": ("device_id", "did", "设备"),
    "channel": ("channel", "ch", "渠道"),
    "event_type": ("event_type", "event", "事件"),
    "event_time": ("event_time", "ts", "事件时间"),
    "ingested_at": ("ingested_at", "received_at", "入库时间"),
    "cost": ("cost", "spend", "成本"),
    "order_id": ("order_id", "oid", "订单号"),
    "gmv": ("gmv", "amount", "成交额"),
    "order_time": ("order_time", "created_at", "下单时间"),
    "unit": ("unit", "currency_unit", "单位"),
    "alias_id": ("alias_id", "device", "别名"),
    "canonical_id": ("canonical_id", "owner", "主账号"),
}

SPECS = {
    "touch": (TOUCH_COLUMNS, 8),
    "order": (ORDER_COLUMNS, 5),
    "alias": (ALIAS_COLUMNS, 2),
}


def spec(kind: str) -> tuple[tuple[str, ...], int]:
    return SPECS[kind]


def map_header(header: list[str], kind: str) -> dict:
    """把实际表头映射为 {规范列名: 列下标}，并报告缺失与额外列。"""
    columns, _ = SPECS[kind]
    index: dict[str, int] = {}
    extra = []
    for position, name in enumerate(header):
        canonical = None
        for target, names in RENAMES.items():
            if name in names and target in columns:
                canonical = target
                break
        if canonical is None:
            extra.append(name)
            continue
        if canonical not in index:
            index[canonical] = position
    missing = [name for name in columns if name not in index]
    return {"index": index, "missing": missing, "extra": extra,
            "renamed": {raw: target for raw, target in _renames(header, kind)}}


def _renames(header: list[str], kind: str) -> list[tuple[str, str]]:
    columns, _ = SPECS[kind]
    pairs = []
    for name in header:
        for target, names in RENAMES.items():
            if target in columns and name in names and name != target:
                pairs.append((name, target))
    return pairs


def value(row_fields: list[str], mapping: dict, column: str) -> str | None:
    position = mapping["index"].get(column)
    if position is None or position >= len(row_fields):
        return None
    return row_fields[position]
