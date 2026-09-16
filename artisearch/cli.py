"""Typer CLI (Q7)：query / company / graph，读取本地 SQLite 快照，无需联网。

用法示例：
    artisearch query --type supplier --min-relevance 50 --as-of 2026-09
    artisearch company NVDA
    artisearch graph
    artisearch query --company TSM --json
"""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import typer

from . import config, db
from .models import RelationshipType

app = typer.Typer(
    name="artisearch",
    help="NVIDIA (NVDA) 供应链与合作关系研究 CLI（读本地快照，离线运行）",
    no_args_is_help=True,
)


def _parse_as_of(value: str) -> date:
    """接受 YYYY-MM（解析为当月最后一天）或 YYYY-MM-DD。"""
    try:
        d = datetime.strptime(value, "%Y-%m").date()
        return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass
    raise typer.BadParameter("as_of 需为 YYYY-MM 或 YYYY-MM-DD 格式")


def _connect(snapshot: Path) -> sqlite3.Connection:
    if not snapshot.exists():
        raise typer.BadParameter(
            f"快照不存在: {snapshot}\n请先运行 `uv run python scripts/collect.py` 生成快照。"
        )
    return db.connect(snapshot)


def _echo_relationships(rels, total: int) -> None:
    typer.echo(
        f"{'ID':38s} {'TYPE':20s} {'DIR':18s} {'STATUS':10s} {'SCORE':6s} OBJECT"
    )
    typer.echo("-" * 110)
    for r in rels:
        typer.echo(
            f"{r.id:38s} {r.relationship_type.value:20s} {r.direction.value:18s} "
            f"{r.status.value:10s} {r.confidence_score:<6d} {r.object}"
        )
    typer.echo("-" * 110)
    typer.echo(f"共 {total} 条")


# ---------------------------------------------------------------------------
@app.command()
def query(
    company: str = typer.Option(None, help="按公司 ticker 过滤（如 NVDA / TSM）"),
    type: str = typer.Option(None, help="关系类型: supplier|customer|partner|investor_or_investee|peer"),
    min_relevance: int = typer.Option(None, min=0, max=100, help="最低置信度 0-100"),
    as_of: str = typer.Option(None, help="研究截点 YYYY-MM 或 YYYY-MM-DD"),
    page: int = typer.Option(1, min=1, help="页码"),
    page_size: int = typer.Option(20, min=1, max=100, help="每页条数"),
    snapshot: Path = typer.Option(config.SNAPSHOT_PATH, help="快照路径"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查询关系列表（支持类型/置信度/截点过滤与分页）。"""
    rel_type: RelationshipType | None = None
    if type is not None:
        try:
            rel_type = RelationshipType(type)
        except ValueError:
            raise typer.BadParameter(
                f"非法关系类型: {type}（可选: {[t.value for t in RelationshipType]}）"
            )
    as_of_date = _parse_as_of(as_of) if as_of else None

    conn = _connect(snapshot)
    try:
        items, total = db.list_relationships(
            conn,
            company=company,
            relationship_type=rel_type,
            min_relevance=min_relevance,
            as_of=as_of_date,
            page=page,
            page_size=page_size,
        )
    finally:
        conn.close()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "items": [r.model_dump(mode="json") for r in items],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _echo_relationships(items, total)


# ---------------------------------------------------------------------------
@app.command()
def company(
    ticker: str = typer.Argument(..., help="公司 ticker，如 NVDA"),
    snapshot: Path = typer.Option(config.SNAPSHOT_PATH, help="快照路径"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查看公司画像及其全部关系与评分明细。"""
    conn = _connect(snapshot)
    try:
        profile = db.get_company(conn, ticker.upper())
        if profile is None:
            raise typer.BadParameter(f"未知公司 ticker: {ticker}")
        rels, total = db.list_relationships(conn, company=ticker, page_size=100)
    finally:
        conn.close()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "company": profile.model_dump(mode="json"),
                    "relationships": [r.model_dump(mode="json") for r in rels],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(f"{profile.ticker} — {profile.name}")
    typer.echo(f"  交易所: {profile.exchange or 'N/A'} | 证券标识: {profile.security_id or 'N/A'} | 上市: {'是' if profile.is_public else '否'}")
    typer.echo(f"  生态角色: {profile.role_in_ecosystem or 'N/A'}")
    typer.echo()
    _echo_relationships(rels, total)


# ---------------------------------------------------------------------------
@app.command()
def graph(
    snapshot: Path = typer.Option(config.SNAPSHOT_PATH, help="快照路径"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """输出关系图数据（nodes/edges）。"""
    conn = _connect(snapshot)
    try:
        g = db.get_graph(conn)
    finally:
        conn.close()

    if json_output:
        typer.echo(json.dumps(g.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return

    typer.echo(f"关系图（as_of={g.as_of}）：{len(g.nodes)} 节点 / {len(g.edges)} 边")
    typer.echo("\n节点:")
    for n in g.nodes:
        mark = " ★" if n.is_subject else ""
        typer.echo(f"  {n.id:12s} {n.label}{mark}")
    typer.echo("\n边:")
    for e in g.edges:
        typer.echo(
            f"  {e.source:6s} --[{e.relationship_type.value} / {e.direction} / {e.confidence_score}]--> {e.target}"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
