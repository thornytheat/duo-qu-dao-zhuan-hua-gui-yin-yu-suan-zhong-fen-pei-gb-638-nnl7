"""有放回抽样：确定性线性同余，同种子同结果。

朴素实现用包内随机数或系统时间做种子，同一份证据两次跑出两个区间，无法复核。
这里把状态与步数写死，跨进程、跨机器都是同一串下标。
"""
from __future__ import annotations

MASK = (1 << 64) - 1
MULT = 6364136223846793005
INC = 1442695040888963407

MIN_RESAMPLES = 20
MAX_RESAMPLES = 2000
MAX_SEED = 2 ** 31 - 1


def stream(seed: int):
    """下标流：每步取高 31 位，低位周期太短。"""
    state = (int(seed) & MASK) or 1
    while True:
        state = (state * MULT + INC) & MASK
        yield (state >> 33) & 0x7FFFFFFF


def indices(count: int, size: int, seed: int) -> list[int]:
    if count <= 0 or size <= 0:
        return []
    source = stream(seed)
    return [next(source) % count for _ in range(int(size))]


def run(clusters: list[dict], channels: tuple[str, ...], resamples: int,
        seed: int) -> dict:
    """抽 resamples 次，每次抽 len(clusters) 个簇（有放回），累计每渠道金额。"""
    count = len(clusters)
    if count == 0:
        return {"resamples": 0, "seed": int(seed), "picks_per_draw": 0, "draws": []}
    source = stream(seed)
    draws = []
    for _ in range(int(resamples)):
        totals = {code: 0 for code in channels}
        total_cents = 0
        for _ in range(count):
            cluster = clusters[next(source) % count]
            total_cents += int(cluster["gmv_cents"])
            for code, cents in cluster["channels"].items():
                totals[code] = totals.get(code, 0) + int(cents)
        draws.append({"total_cents": total_cents, "channels": totals})
    return {"resamples": int(resamples), "seed": int(seed),
            "picks_per_draw": count, "draws": draws}


def top_channel(draw: dict) -> str:
    """一次抽样的首位渠道：金额降序，同额取渠道代码升序。"""
    ranked = sorted(draw["channels"].items(), key=lambda item: (-int(item[1]), item[0]))
    return ranked[0][0] if ranked else "—"
