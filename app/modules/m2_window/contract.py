"""字段契约：列名、类型、单位、精度、可空、窗口与模型清单。

页面上的每个数字都能在这张表里找到含义，规则版本随运行清单一起落库。
"""
from __future__ import annotations

CHANNELS = (
    ("douyin_feed", "抖音信息流"),
    ("xhs_seed", "小红书种草"),
    ("wechat_moments", "微信朋友圈"),
    ("baidu_search", "百度搜索"),
    ("tmall_ads", "天猫直通车"),
    ("private_group", "私域社群"),
)
CHANNEL_LABELS = dict(CHANNELS)
CHANNEL_CODES = tuple(code for code, _ in CHANNELS)

EVENT_TYPES = {"click": "点击", "impression": "曝光"}

# 金额与用量的最小刻度：1 分；对外一律给整数分或两位小数字符串
AMOUNT_UNIT = "分"
MONEY_DECIMALS = 2

SENTINELS = frozenset({
    "", "-", "—", "--", "N/A", "NA", "NULL", "NONE", "NAN", "未知", "无", "空",
})

COLUMNS = {
    "touch": (
        {"name": "touch_id", "type": "string", "unit": "—", "nullable": False,
         "note": "触点编号，保留前导零"},
        {"name": "user_id", "type": "string", "unit": "—", "nullable": False,
         "note": "设备号或账号，经别名表合并到主账号"},
        {"name": "device_id", "type": "string", "unit": "—", "nullable": True,
         "note": "设备号，仅作证据"},
        {"name": "channel", "type": "enum", "unit": "—", "nullable": False,
         "note": "六个投放渠道之一"},
        {"name": "event_type", "type": "enum", "unit": "—", "nullable": False,
         "note": "click / impression"},
        {"name": "event_time", "type": "timestamp", "unit": "带偏移", "nullable": False,
         "note": "归一为 UTC 后参与窗口判定"},
        {"name": "ingested_at", "type": "timestamp", "unit": "带偏移", "nullable": True,
         "note": "入库时间，晚于订单时间即标记迟到"},
        {"name": "cost", "type": "decimal", "unit": "元", "nullable": True,
         "note": "该触点成本，入库按分取整"},
    ),
    "order": (
        {"name": "order_id", "type": "string", "unit": "—", "nullable": False,
         "note": "订单号，按字符串保形"},
        {"name": "user_id", "type": "string", "unit": "—", "nullable": False,
         "note": "下单账号"},
        {"name": "gmv", "type": "decimal", "unit": "元 / 万元", "nullable": False,
         "note": "成交额，单位列优先于数值后缀"},
        {"name": "order_time", "type": "timestamp", "unit": "带偏移", "nullable": False,
         "note": "窗口右端，开区间"},
        {"name": "unit", "type": "enum", "unit": "—", "nullable": True,
         "note": "元 / 万元，缺省按元"},
    ),
    "alias": (
        {"name": "alias_id", "type": "string", "unit": "—", "nullable": False,
         "note": "设备号或旧账号"},
        {"name": "canonical_id", "type": "string", "unit": "—", "nullable": False,
         "note": "别名指向的另一标识"},
    ),
}


def payload(settings) -> dict:
    return {
        "rule_version": settings.rule_version,
        "channels": [{"code": code, "label": label} for code, label in CHANNELS],
        "event_types": [{"code": code, "label": label} for code, label in EVENT_TYPES.items()],
        "windows": [
            {"event_type": "click", "days": settings.click_window.days,
             "interval": "[t-7d, t)"},
            {"event_type": "impression", "days": settings.impression_window.days,
             "interval": "[t-1d, t)"},
        ],
        "columns": {kind: list(cols) for kind, cols in COLUMNS.items()},
        "sentinels": sorted(SENTINELS),
        "money": {"unit": AMOUNT_UNIT, "decimals": MONEY_DECIMALS,
                  "storage": "整数分"},
        "dedupe_key": {"touch": "touch_id", "order": "order_id", "alias": "alias_id"},
        "late_rule": "入库时间晚于订单时间 → 标记迟到，仍计入",
        "models": [
            {"code": "first", "label": "首次触点"},
            {"code": "last", "label": "末次触点"},
            {"code": "linear", "label": "线性均分"},
            {"code": "time_decay", "label": "时间衰减"},
            {"code": "position", "label": "位置加权"},
        ],
    }
