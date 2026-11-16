"""测试固定装置：临时库、固定样例目录、TestClient。

时间断言只用样例里的固定时刻（2026-11-05 起），不读当前时间。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
for candidate in (ROOT, ROOT / "tests"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app import config                      # noqa: E402
from app.main import create_app             # noqa: E402
from app.modules.m1_ingest import service as m1_service  # noqa: E402
from app.store import Store                 # noqa: E402


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(db_path=tmp_path / "mixcredit.sqlite",
                           sample_dir=ROOT / "sample_data")


@pytest.fixture()
def store(settings) -> Store:
    instance = Store(settings.db_path)
    instance.init()
    return instance


@pytest.fixture()
def dirty(settings, store) -> dict:
    return m1_service.run(settings, store, "dirty")


@pytest.fixture()
def run_id(dirty) -> str:
    return dirty["run_id"]


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def client_dirty(client) -> TestClient:
    response = client.post("/api/import", json={"dataset": "dirty"})
    assert response.status_code == 200
    return client
