"""配置：样例目录、SQLite 路径、窗口与模型参数、预算约束。

规则函数只接收 Settings 对象，不自己读环境变量；测试可以逐用例构造。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 固定 +08:00 偏移，容器里没有 tzdata 也能算出同一个时刻
ADMIN_TZ = timezone(timedelta(hours=8))

# 归一化后的最小金额刻度：1 分
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    sample_dir: Path
    rule_version: str = "mixcredit-1.0.0"
    click_window: timedelta = timedelta(days=7)
    impression_window: timedelta = timedelta(days=1)
    decay_half_life_days: Decimal = Decimal("3.5")
    budget_step_cents: int = 100_000          # 每次加投 1000 元
    marginal_decay: Decimal = Decimal("0.15")  # 同一渠道每加一档，边际 ROI 衰减 15%
    plan_versions: tuple[str, ...] = ("v1", "v2", "v3")

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.environ.get("MIXCREDIT_DB", str(ROOT / "data" / "mixcredit.sqlite")))
        sample_dir = Path(os.environ.get("MIXCREDIT_SAMPLE_DIR", str(ROOT / "sample_data")))
        return cls(db_path=db_path, sample_dir=sample_dir)

    def window_for(self, event_type: str) -> timedelta:
        return self.impression_window if event_type == "impression" else self.click_window


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
