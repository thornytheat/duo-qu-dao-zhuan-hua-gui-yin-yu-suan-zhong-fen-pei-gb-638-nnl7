"""取值归一：千分位、全角数字、万元后缀、空值哨兵、时区偏移。

金额先归一到元（Decimal），再按分取整；时间先归一到 UTC。
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ...config import ADMIN_TZ
from .contract import SENTINELS

CENT = Decimal("0.01")
WAN = Decimal("10000")


def normalize_text(raw: str | None) -> str:
    if raw is None:
        return ""
    return unicodedata.normalize("NFKC", str(raw)).strip()


def is_sentinel(raw: str | None) -> bool:
    text = normalize_text(raw)
    if text in SENTINELS:
        return True
    return text.upper() in {item.upper() for item in SENTINELS if item.isascii()}


def parse_amount(raw: str | None, unit: str = "元") -> dict:
    """返回 {status, amount(元 Decimal), cents, reason}。

    status: ok / sentinel / invalid
    """
    text = normalize_text(raw)
    unit_text = normalize_text(unit) or "元"
    if is_sentinel(raw):
        return {"status": "sentinel", "amount": None, "cents": None,
                "reason": "空值哨兵"}
    multiplier = Decimal(1)
    if unit_text in {"万元", "万"}:
        multiplier = WAN
    elif unit_text in {"分", "cent"}:
        multiplier = CENT
    if text.endswith(("万元", "万")):
        text = text[:-2] if text.endswith("万元") else text[:-1]
        multiplier = WAN
    text = text.replace(",", "").replace("，", "").replace("元", "")
    if text.startswith("¥"):
        text = text[1:]
    try:
        amount = Decimal(text) * multiplier
    except (InvalidOperation, ValueError):
        return {"status": "invalid", "amount": None, "cents": None,
                "reason": "无法解析为金额"}
    if amount < 0:
        return {"status": "invalid", "amount": None, "cents": None,
                "reason": "负金额"}
    cents = int((amount.quantize(CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())
    return {"status": "ok", "amount": amount, "cents": cents, "reason": ""}


def parse_ts(raw: str | None) -> dict:
    """返回 {status, utc, kind, offset}，kind: aware / naive / invalid。"""
    text = normalize_text(raw)
    if not text or is_sentinel(raw):
        return {"status": "invalid", "utc": None, "kind": "invalid", "offset": ""}
    candidate = text.replace(" ", "T", 1) if "T" not in text else text
    if candidate.endswith("z") or candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return {"status": "invalid", "utc": None, "kind": "invalid", "offset": ""}
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ADMIN_TZ)
        kind = "naive"
    else:
        kind = "aware"
    utc = moment.astimezone(timezone.utc)
    offset = moment.strftime("%z")
    return {"status": "ok", "utc": utc, "kind": kind,
            "offset": (offset[:3] + ":" + offset[3:]) if offset else "+00:00"}


def to_utc_iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_channel(raw: str | None, known: tuple[str, ...]) -> str | None:
    text = normalize_text(raw).lower()
    if not text or text in {"none", "null", "-"}:
        return None
    return text if text in known else None


def parse_event_type(raw: str | None) -> str | None:
    text = normalize_text(raw).lower()
    return text if text in {"click", "impression"} else None
