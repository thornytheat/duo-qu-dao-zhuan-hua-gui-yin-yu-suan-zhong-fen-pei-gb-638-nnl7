"""分位数与区间：份额、ROI 的上下限，以及「建议是否成立」的判定。

分位取排序后的第 floor(q×(N-1)) 个（零基，向下取整），区间是 [下限, 上限]。
判定四条：簇数太少、区间过宽、首位不稳、前两渠道区间重叠；任一命中即「不稳」。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

STEP = Decimal("0.01")
HUNDRED = Decimal(100)

MIN_CLUSTERS = 8
MAX_HALF_WIDTH_PP = Decimal("5.00")
MIN_TOP_SHARE_PCT = Decimal("90.00")

DEFAULT_ALPHA_PCT = 10
MIN_ALPHA_PCT = 1
MAX_ALPHA_PCT = 50


def quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(STEP, rounding=ROUND_HALF_UP)


def quantile(values: list[Decimal], q: Decimal):
    """values 已升序；返回第 floor(q×(N-1)) 个。"""
    if not values:
        return None
    index = int(Decimal(q) * (len(values) - 1))
    return values[max(0, min(index, len(values) - 1))]


def share_pct(cents: int, total_cents: int) -> Decimal:
    if total_cents <= 0:
        return Decimal("0.00")
    return quantize(Decimal(int(cents)) * HUNDRED / Decimal(int(total_cents)))


def roi(cents: int, spend_cents: int):
    if spend_cents <= 0:
        return None
    return quantize(Decimal(int(cents)) / Decimal(int(spend_cents)))


def channel_rows(clusters: list[dict], draws: list[dict], spend: dict,
                 alpha_pct: int = DEFAULT_ALPHA_PCT) -> list[dict]:
    codes = sorted({code for cluster in clusters for code in cluster["channels"]})
    point_total = sum(int(cluster["gmv_cents"]) for cluster in clusters)
    half = Decimal(alpha_pct) / Decimal(200)
    upper = Decimal(1) - half
    rows = []
    for code in codes:
        point_cents = sum(int(cluster["channels"].get(code, 0)) for cluster in clusters)
        shares = sorted(share_pct(int(draw["channels"].get(code, 0)),
                                  int(draw["total_cents"])) for draw in draws)
        values = sorted(roi(int(draw["channels"].get(code, 0)),
                            int(spend.get(code, {}).get("cost_cents", 0)))
                        for draw in draws)
        values = [value for value in values if value is not None]
        spend_cents = int(spend.get(code, {}).get("cost_cents", 0))
        share_lo = quantile(shares, half)
        share_hi = quantile(shares, upper)
        rows.append({
            "channel": code,
            "point_cents": point_cents,
            "share_pct": share_pct(point_cents, point_total),
            "share_lo": share_lo if share_lo is not None else Decimal("0.00"),
            "share_hi": share_hi if share_hi is not None else Decimal("0.00"),
            "half_width_pp": quantize(((share_hi if share_hi is not None else Decimal(0))
                                       - (share_lo if share_lo is not None else Decimal(0)))
                                      / Decimal(2)),
            "roi": roi(point_cents, spend_cents),
            "roi_lo": quantile(values, half),
            "roi_hi": quantile(values, upper),
            "spend_cents": spend_cents,
        })
    rows.sort(key=lambda row: (-row["point_cents"], row["channel"]))
    return rows


def top_counts(draws: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for draw in draws:
        ranked = sorted(draw["channels"].items(), key=lambda item: (-int(item[1]), item[0]))
        if not ranked:
            continue
        code = ranked[0][0]
        counts[code] = counts.get(code, 0) + 1
    return counts


def assess(rows: list[dict], counts: dict, resamples: int,
           clusters: int) -> dict:
    codes: list[str] = []
    max_half = max((row["half_width_pp"] for row in rows), default=Decimal("0.00"))
    top = rows[0] if rows else None
    top_share = (quantize(Decimal(counts.get(top["channel"], 0)) * HUNDRED
                          / Decimal(int(resamples)))
                 if top and resamples else Decimal("0.00"))
    if clusters < MIN_CLUSTERS:
        codes.append("CI_FRAME_THIN")
    if max_half > MAX_HALF_WIDTH_PP:
        codes.append("CI_WIDE_INTERVAL")
    if top is not None and top_share < MIN_TOP_SHARE_PCT:
        codes.append("CI_UNSTABLE_RANK")
    if len(rows) >= 2 and rows[0]["share_lo"] <= rows[1]["share_hi"]:
        codes.append("CI_INTERVAL_OVERLAP")
    return {
        "status": "成立" if not codes else "不稳",
        "codes": codes,
        "top_channel": top["channel"] if top else None,
        "top_stay_pct": top_share,
        "max_half_width_pp": max_half,
        "runner_up": rows[1]["channel"] if len(rows) >= 2 else None,
    }


def top_stay_pct(counts: dict, code: str, resamples: int) -> Decimal:
    if resamples <= 0:
        return Decimal("0.00")
    return quantize(Decimal(counts.get(code, 0)) * HUNDRED / Decimal(int(resamples)))
