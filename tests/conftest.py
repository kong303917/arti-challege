"""Shared fixtures: build a temp SQLite snapshot from the JSON golden fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from artisearch import db
from artisearch.api import create_app
from artisearch.models import Company

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SNAPSHOT_JSON = FIXTURES_DIR / "snapshot.json"


@pytest.fixture(scope="session")
def snapshot_db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("snap") / "snapshot.sqlite"
    db.build_snapshot_from_json(SNAPSHOT_JSON, path, scored=False)
    return path


@pytest.fixture()
def conn(snapshot_db_path):
    conn = db.connect(snapshot_db_path)
    yield conn
    conn.close()


@pytest.fixture()
def client(snapshot_db_path) -> TestClient:
    app = create_app(snapshot_db_path)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def empty_db_path(tmp_path_factory) -> Path:
    """只有公司、没有关系的空库（空图边界测试）。"""
    payload = json.loads(SNAPSHOT_JSON.read_text(encoding="utf-8"))
    path = tmp_path_factory.mktemp("empty") / "empty.sqlite"
    conn = db.connect(path)
    db.init_db(conn)
    for c in payload["companies"]:
        db.upsert_company(conn, Company(**c))
    conn.close()
    return path


@pytest.fixture()
def empty_client(empty_db_path) -> TestClient:
    app = create_app(empty_db_path)
    with TestClient(app) as c:
        yield c
