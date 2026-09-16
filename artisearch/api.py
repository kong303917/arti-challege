"""FastAPI HTTP JSON API (Q7)。

端点：
- GET /api/companies/{ticker}            公司画像 + 相关关系及评分明细
- GET /api/relationships                 过滤（company/type/min_relevance/as_of）+ 分页
- GET /api/relationships/{id}            单条关系 + evidence
- GET /api/relationships/{id}/evidence   单条关系的证据链
- GET /api/graph                         nodes/edges 关系图数据
- GET /api/health                        健康检查

校验：非法 type / 分页参数 → 422（pydantic/FastAPI 原生）；
未知 ticker / 关系 id → 404（显式 handler）；空图 → 明确空响应。
只读本地 SQLite 快照，无需联网。
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import config, db
from .models import (
    Company,
    Direction,
    Evidence,
    GraphResponse,
    Relationship,
    RelationshipPage,
    RelationshipType,
)

MAX_PAGE_SIZE = 100


def create_app(snapshot_path: str | Path | None = None) -> FastAPI:
    path = Path(snapshot_path) if snapshot_path else Path(config.SNAPSHOT_PATH)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not path.exists():
            raise RuntimeError(
                f"快照不存在: {path}。请先运行 `uv run python scripts/collect.py` 生成快照。"
            )
        app.state.conn = db.connect(path)
        yield
        app.state.conn.close()

    app = FastAPI(
        title="ARTi Supply-Chain Research API",
        description="NVIDIA (NVDA) 供应链与合作关系研究服务 — 合法公开资料 + 可解释评分",
        version="0.1.0",
        lifespan=lifespan,
    )

    def get_conn() -> sqlite3.Connection:
        return app.state.conn

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # ------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "as_of": config.AS_OF.isoformat(), "snapshot": str(path)}

    # ------------------------------------------------------------------
    @app.get("/api/companies/{ticker}", response_model=Company, responses={404: {"description": "未知 ticker"}})
    def get_company(ticker: str) -> Company:
        company = db.get_company(get_conn(), ticker)
        if company is None:
            raise HTTPException(status_code=404, detail=f"未知公司 ticker: {ticker}")
        return company

    # ------------------------------------------------------------------
    @app.get(
        "/api/relationships",
        response_model=RelationshipPage,
        responses={422: {"description": "非法 type / 分页 / 日期参数"}},
    )
    def list_relationships(
        company: str | None = Query(None, description="按公司 ticker 过滤（subject 或 object）"),
        type: RelationshipType | None = Query(None, description="关系类型"),
        min_relevance: int | None = Query(None, ge=0, le=100, description="最低置信度 0-100"),
        as_of: date | None = Query(None, description="研究截点（仅返回 as_of 不晚于该日期的关系）"),
        page: int = Query(1, ge=1, description="页码（从 1 开始）"),
        page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
    ) -> RelationshipPage:
        items, total = db.list_relationships(
            get_conn(),
            company=company,
            relationship_type=type,
            min_relevance=min_relevance,
            as_of=as_of,
            page=page,
            page_size=page_size,
        )
        return RelationshipPage(total=total, page=page, page_size=page_size, items=items)

    # ------------------------------------------------------------------
    @app.get("/api/relationships/{rel_id}", response_model=Relationship, responses={404: {"description": "未知关系 id"}})
    def get_relationship(rel_id: str) -> Relationship:
        rel = db.get_relationship(get_conn(), rel_id)
        if rel is None:
            raise HTTPException(status_code=404, detail=f"未知关系 id: {rel_id}")
        return rel

    # ------------------------------------------------------------------
    @app.get("/api/relationships/{rel_id}/evidence", response_model=list[Evidence], responses={404: {"description": "未知关系 id"}})
    def get_evidence(rel_id: str) -> list[Evidence]:
        evidence = db.get_evidence_for(get_conn(), rel_id)
        if evidence is None:
            raise HTTPException(status_code=404, detail=f"未知关系 id: {rel_id}")
        return evidence

    # ------------------------------------------------------------------
    @app.get("/api/graph", response_model=GraphResponse)
    def graph() -> GraphResponse:
        """关系图数据（nodes/edges）；无关系时返回空列表（明确空响应）。"""
        return db.get_graph(get_conn())

    return app


app = create_app()
