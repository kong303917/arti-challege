"""SQLite data access layer: schema init, upserts, and read APIs shared by
the FastAPI service and the Typer CLI.

Tables: companies / relationships / evidence (evidence rows reference
relationships.id). Score breakdown is stored as JSON inside relationships rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from . import config
from .models import (
    Company,
    Direction,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphResponse,
    Quantification,
    Relationship,
    RelationshipInput,
    RelationshipStatus,
    RelationshipType,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker            TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    exchange          TEXT,
    security_id       TEXT,
    cik               TEXT,
    is_public         INTEGER NOT NULL DEFAULT 1,
    role_in_ecosystem TEXT
);

CREATE TABLE IF NOT EXISTS relationships (
    id                  TEXT PRIMARY KEY,
    subject             TEXT NOT NULL,
    object              TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,
    direction           TEXT NOT NULL,
    status              TEXT NOT NULL,
    as_of               TEXT NOT NULL,
    quantification      TEXT NOT NULL DEFAULT 'qualitative',
    confidence_score    INTEGER NOT NULL,
    score_breakdown     TEXT NOT NULL,
    uncertainty_notes   TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id   TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    source_url        TEXT NOT NULL,
    publisher         TEXT NOT NULL,
    published_at      TEXT,
    accessed_at       TEXT NOT NULL,
    evidence_locator  TEXT NOT NULL,
    access_license    TEXT NOT NULL DEFAULT 'public',
    note              TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_rel ON evidence(relationship_id);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relationships(subject);
CREATE INDEX IF NOT EXISTS idx_rel_object ON relationships(object);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relationship_type);
"""


def connect(path: str | Path = config.SNAPSHOT_PATH) -> sqlite3.Connection:
    # check_same_thread=False：FastAPI 在线程池中执行同步端点；
    # 本服务为只读快照查询、无并发写，可安全跨线程共享连接。
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Writes (used by collect / snapshot builder; idempotent upserts)
# ---------------------------------------------------------------------------
def upsert_company(conn: sqlite3.Connection, company: Company) -> None:
    conn.execute(
        """
        INSERT INTO companies (ticker, name, exchange, security_id, cik, is_public, role_in_ecosystem)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            name=excluded.name, exchange=excluded.exchange,
            security_id=excluded.security_id, cik=excluded.cik,
            is_public=excluded.is_public, role_in_ecosystem=excluded.role_in_ecosystem
        """,
        (
            company.ticker.upper(),
            company.name,
            company.exchange,
            company.security_id,
            company.cik,
            int(company.is_public),
            company.role_in_ecosystem,
        ),
    )
    conn.commit()


def insert_relationship(
    conn: sqlite3.Connection, relationship: Relationship
) -> None:
    """Insert (or replace) a scored relationship plus its evidence rows."""
    conn.execute(
        """
        INSERT OR REPLACE INTO relationships
            (id, subject, object, relationship_type, direction, status, as_of,
             quantification, confidence_score, score_breakdown, uncertainty_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            relationship.id,
            relationship.subject,
            relationship.object,
            relationship.relationship_type.value,
            relationship.direction.value,
            relationship.status.value,
            relationship.as_of.isoformat(),
            relationship.quantification.value,
            relationship.confidence_score,
            relationship.score_breakdown.model_dump_json(),
            relationship.uncertainty_notes,
        ),
    )
    conn.execute("DELETE FROM evidence WHERE relationship_id = ?", (relationship.id,))
    for ev in relationship.evidence:
        conn.execute(
            """
            INSERT INTO evidence
                (relationship_id, source_url, publisher, published_at, accessed_at,
                 evidence_locator, access_license, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relationship.id,
                ev.source_url,
                ev.publisher,
                ev.published_at.isoformat() if ev.published_at else None,
                ev.accessed_at.isoformat(),
                ev.evidence_locator,
                ev.access_license,
                ev.note,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _row_to_relationship(row: sqlite3.Row, evidence_rows: list[sqlite3.Row]) -> Relationship:
    return Relationship(
        id=row["id"],
        subject=row["subject"],
        object=row["object"],
        relationship_type=RelationshipType(row["relationship_type"]),
        direction=Direction(row["direction"]),
        status=RelationshipStatus(row["status"]),
        as_of=date.fromisoformat(row["as_of"]),
        quantification=Quantification(row["quantification"]),
        confidence_score=row["confidence_score"],
        score_breakdown=json.loads(row["score_breakdown"]),
        evidence=[
            Evidence(
                source_url=e["source_url"],
                publisher=e["publisher"],
                published_at=date.fromisoformat(e["published_at"]) if e["published_at"] else None,
                accessed_at=date.fromisoformat(e["accessed_at"]),
                evidence_locator=e["evidence_locator"],
                access_license=e["access_license"],
                note=e["note"],
            )
            for e in evidence_rows
        ],
        uncertainty_notes=row["uncertainty_notes"],
    )


def get_company(conn: sqlite3.Connection, ticker: str) -> Company | None:
    row = conn.execute(
        "SELECT * FROM companies WHERE UPPER(ticker) = ?", (ticker.upper(),)
    ).fetchone()
    if row is None:
        return None
    return Company(
        ticker=row["ticker"],
        name=row["name"],
        exchange=row["exchange"],
        security_id=row["security_id"],
        cik=row["cik"],
        is_public=bool(row["is_public"]),
        role_in_ecosystem=row["role_in_ecosystem"],
    )


def list_companies(conn: sqlite3.Connection) -> list[Company]:
    rows = conn.execute("SELECT * FROM companies ORDER BY ticker").fetchall()
    return [
        Company(
            ticker=r["ticker"],
            name=r["name"],
            exchange=r["exchange"],
            security_id=r["security_id"],
            cik=r["cik"],
            is_public=bool(r["is_public"]),
            role_in_ecosystem=r["role_in_ecosystem"],
        )
        for r in rows
    ]


def _load_relationships(
    conn: sqlite3.Connection,
    where: str = "",
    params: tuple = (),
    limit: int | None = None,
    offset: int = 0,
    order_by: str = "confidence_score DESC, id ASC",
) -> list[Relationship]:
    sql = f"SELECT * FROM relationships {where} ORDER BY {order_by}"
    if limit is not None:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    ev_rows = conn.execute(
        f"SELECT * FROM evidence WHERE relationship_id IN ({placeholders}) ORDER BY id",
        ids,
    ).fetchall()
    ev_map: dict[str, list[sqlite3.Row]] = {}
    for e in ev_rows:
        ev_map.setdefault(e["relationship_id"], []).append(e)
    return [_row_to_relationship(r, ev_map.get(r["id"], [])) for r in rows]


def list_relationships(
    conn: sqlite3.Connection,
    company: str | None = None,
    relationship_type: RelationshipType | None = None,
    min_relevance: int | None = None,
    as_of: date | None = None,
    status: RelationshipStatus | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Relationship], int]:
    """Filter + paginate; returns (items, total)."""
    clauses: list[str] = []
    params: list = []
    if company:
        clauses.append("(UPPER(subject) = ? OR UPPER(object) = ?)")
        c = company.upper()
        params.extend([c, c])
    if relationship_type:
        clauses.append("relationship_type = ?")
        params.append(relationship_type.value)
    if min_relevance is not None:
        clauses.append("confidence_score >= ?")
        params.append(int(min_relevance))
    if as_of:
        clauses.append("as_of <= ?")
        params.append(as_of.isoformat())
    if status:
        clauses.append("status = ?")
        params.append(status.value)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM relationships {where}", params
    ).fetchone()[0]
    items = _load_relationships(
        conn, where, tuple(params), limit=page_size, offset=(page - 1) * page_size
    )
    return items, total


def get_relationship(conn: sqlite3.Connection, rel_id: str) -> Relationship | None:
    items = _load_relationships(conn, "WHERE id = ?", (rel_id,), limit=1)
    return items[0] if items else None


def get_evidence_for(conn: sqlite3.Connection, rel_id: str) -> list[Evidence] | None:
    rel = get_relationship(conn, rel_id)
    if rel is None:
        return None
    return rel.evidence


def get_graph(conn: sqlite3.Connection) -> GraphResponse:
    """Build nodes/edges for the relationship graph (Q7: /api/graph)."""
    rels = _load_relationships(conn)
    companies = {c.ticker: c for c in list_companies(conn)}

    nodes: dict[str, GraphNode] = {}
    for rel in rels:
        for entity in (rel.subject, rel.object):
            if entity not in nodes:
                label = companies[entity].name if entity in companies else entity
                nodes[entity] = GraphNode(
                    id=entity, label=label, is_subject=(entity == config.NVDA_TICKER)
                )
    edges = [
        GraphEdge(
            id=r.id,
            source=r.subject,
            target=r.object,
            relationship_type=r.relationship_type,
            direction=r.direction,
            confidence_score=r.confidence_score,
        )
        for r in rels
    ]
    return GraphResponse(
        as_of=config.AS_OF,
        nodes=sorted(nodes.values(), key=lambda n: (not n.is_subject, n.id)),
        edges=edges,
    )


# ---------------------------------------------------------------------------
# Snapshot builder — load a JSON fixture (list of RelationshipInput) into a db
# ---------------------------------------------------------------------------
def build_snapshot_from_json(
    snapshot_json_path: str | Path,
    db_path: str | Path,
    scored: bool = True,
) -> None:
    """Create db_path from a JSON file.

    If ``scored`` is True the JSON contains full Relationship objects (with
    scores); otherwise it contains RelationshipInput objects which are scored
    on the fly via score.score_relationship.
    """
    from . import score  # local import to avoid cycle

    payload = json.loads(Path(snapshot_json_path).read_text(encoding="utf-8"))
    companies = [Company(**c) for c in payload.get("companies", [])]
    rel_data = payload.get("relationships", [])

    conn = connect(db_path)
    try:
        init_db(conn)
        for c in companies:
            upsert_company(conn, c)
        for item in rel_data:
            if scored:
                rel = Relationship.model_validate(item)
            else:
                rel = score.score_relationship(RelationshipInput.model_validate(item))
            insert_relationship(conn, rel)
    finally:
        conn.close()
