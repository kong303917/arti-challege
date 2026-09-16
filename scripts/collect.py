"""采集入口：可重跑（幂等），守 robots / 限速，生成 data/snapshot.sqlite。

流程：
1. 拉取 NVDA 最新 10-K（submissions API 定位 → 拉取全文）；
2. 按人工标注层(annotations.py)在 10-K 中定位实体提及，抽取证据片段；
3. 对设有交叉验证的关系，用 EDGAR 全文搜索找到对方年报中提及 NVIDIA 的
   最新文件并抽取片段（上下游上市公司各自披露 → 独立信源）；
4. 对 SEC 注册公司用 submissions API 校准交易所信息；
5. 组合标注 + 证据 → 评分引擎打分 → 写入快照；
6. 全部原始响应归档至 data/raw/（含 manifest.jsonl）。

用法：
    uv run python scripts/collect.py [--db data/snapshot.sqlite]

环境变量（无需任何真实凭据）：
    EDGAR_USER_AGENT  SEC 要求的声明式 UA（默认为项目占位值）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from artisearch import annotations, config, db, score  # noqa: E402
from artisearch.collect import (  # noqa: E402
    CollectError,
    EdgarClient,
    FilingInfo,
    fetch_filing_text,
    find_mentions,
    fts_search,
    latest_annual_filing,
    pick_recent_hit,
)
from artisearch.models import Evidence, RelationshipInput  # noqa: E402

TODAY = date.today()
SEC_PUBLISHER = "NVIDIA Corporation SEC filings"


def build_nvda_evidence(filing: FilingInfo, text: str, rel: annotations.CuratedRelation) -> Evidence | None:
    """在 NVDA 10-K 中定位实体提及，返回证据（无提及返回 None）。"""
    for term in rel.nvda_10k_terms:
        snippets = find_mentions(text, term, max_hits=1)
        if snippets:
            return Evidence(
                source_url=filing.url,
                publisher=SEC_PUBLISHER,
                published_at=filing.filing_date,
                accessed_at=TODAY,
                evidence_locator=(
                    f"NVIDIA {filing.form} (FY ending {filing.report_date}, filed "
                    f"{filing.filing_date}) — “{snippets[0]}”"
                ),
                access_license="public (SEC EDGAR)",
                note=f"NVDA 10-K 点名 {rel.object_ticker}：{rel.role_note}",
            )
    return None


def build_cross_evidence(
    client: EdgarClient, rel: annotations.CuratedRelation, company_name: str
) -> tuple[Evidence | None, str | None]:
    """对方 SEC 年报中提及 NVIDIA 的最新文件 → 交叉验证证据。"""
    hits = fts_search(client, "NVIDIA", ciks=rel.cross_cik, forms=rel.cross_forms)
    hit = pick_recent_hit(hits, min_date=rel.cross_min_date)
    if hit is None:
        return None, f"未找到 {company_name} 在 {rel.cross_min_date} 之后的年报中提及 NVIDIA 的记录"
    text = fetch_filing_text(client, hit["url"])
    snippets: list[str] = []
    for term in rel.cross_terms:
        snippets = find_mentions(text, term, max_hits=1, case_insensitive=True)
        if snippets:
            break
    if not snippets:
        return None, None
    evidence = Evidence(
        source_url=hit["url"],
        publisher=f"{company_name} SEC filings",
        published_at=date.fromisoformat(hit["file_date"]),
        accessed_at=TODAY,
        evidence_locator=(
            f"{company_name} filing ({hit['accession']}, filed {hit['file_date']}) — “{snippets[0]}”"
        ),
        access_license="public (SEC EDGAR)",
        note=f"对方披露交叉验证：{rel.role_note}",
    )
    return evidence, None


def calibrate_companies(client: EdgarClient) -> dict[str, dict]:
    """对 SEC 注册公司拉取 submissions，校准交易所与名称（真实官方数据）。"""
    calibrated: dict[str, dict] = {}
    for ticker, company in annotations.CURATED_COMPANIES.items():
        if not company.cik or not company.is_public or company.cik == config.NVDA_CIK:
            continue
        data = client.get_json(f"{config.DATA_SEC_GOV}/submissions/CIK{company.cik}.json")
        exchanges = [e for e in (data.get("exchanges") or []) if e]
        calibrated[ticker] = {
            "name": data.get("name") or company.name,
            "exchange": exchanges[0] if exchanges else company.exchange,
        }
    return calibrated


def main() -> int:
    parser = argparse.ArgumentParser(description="ARTi SEC EDGAR collector")
    parser.add_argument("--db", default=str(config.SNAPSHOT_PATH))
    parser.add_argument("--skip-cross", action="store_true", help="跳过交叉验证（调试用）")
    parser.add_argument("--skip-calibrate", action="store_true", help="跳过交易所校准（调试用）")
    args = parser.parse_args()

    print(f"研究截点 as_of = {config.AS_OF} | 访问日期 = {TODAY}")
    print(f"User-Agent = {config.EDGAR_USER_AGENT}")
    print(f"限速间隔 = {config.EDGAR_REQUEST_INTERVAL_SECONDS}s\n")

    relationships: list[RelationshipInput] = []
    with EdgarClient() as client:
        # 1. NVDA 最新 10-K
        print("[1/4] 获取 NVIDIA 最新 10-K ...")
        nvda_filing = latest_annual_filing(client, config.NVDA_CIK)
        if nvda_filing is None:
            raise CollectError("未找到 NVDA 10-K")
        print(f"      {nvda_filing.form} filed {nvda_filing.filing_date}: {nvda_filing.url}")
        nvda_text = fetch_filing_text(client, nvda_filing.url)

        # 2. 交易所校准
        print("[2/4] 校准 SEC 注册公司交易所信息 ...")
        calibrated = {} if args.skip_calibrate else calibrate_companies(client)
        print(f"      已校准 {len(calibrated)} 家公司")

        # 3. 逐条标注关系 → 证据 → 评分
        print("[3/4] 组装关系与证据（人工标注 + 自动抽取 + 交叉验证）...")
        for rel in annotations.CURATED_RELATIONS:
            evidence: list[Evidence] = []
            nvda_ev = build_nvda_evidence(nvda_filing, nvda_text, rel)
            if nvda_ev:
                evidence.append(nvda_ev)
            cross_note = None
            if rel.cross_cik and not args.skip_cross:
                cross_ev, cross_note = build_cross_evidence(
                    client, rel, annotations.CURATED_COMPANIES[rel.object_ticker].name
                )
                if cross_ev:
                    evidence.append(cross_ev)
            if not evidence:
                print(f"      !! 无任何证据，跳过: {rel.object_ticker}")
                continue
            relationships.append(
                RelationshipInput(
                    id=f"NVDA-{rel.object_ticker}-{rel.relationship_type.value}",
                    subject=config.NVDA_TICKER,
                    object=rel.object_ticker,
                    relationship_type=rel.relationship_type,
                    direction=rel.direction,
                    status=rel.status,
                    as_of=config.AS_OF,
                    quantification=rel.quantification,
                    evidence=evidence,
                    uncertainty_notes=rel.uncertainty_notes,
                )
            )
            sources = " + ".join(e.publisher for e in evidence)
            print(f"      OK {rel.object_ticker:10s} {rel.relationship_type.value:20s} [{sources}]")

        # 4. 写快照
        print(f"[4/4] 评分并写入快照 {args.db} ...")
    scored = [score.score_relationship(r) for r in relationships]

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        for ticker, company in annotations.CURATED_COMPANIES.items():
            profile = company.model_copy(update=calibrated.get(ticker, {}))
            db.upsert_company(conn, profile)
        for rel in scored:
            db.insert_relationship(conn, rel)
    finally:
        conn.close()

    derived = {
        "as_of": config.AS_OF.isoformat(),
        "collected_at": TODAY.isoformat(),
        "companies": [c.model_dump(mode="json") for c in annotations.CURATED_COMPANIES.values()],
        "relationships": [r.model_dump(mode="json") for r in scored],
    }
    derived_path = config.RAW_DIR / "derived_relationships.json"
    derived_path.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：{len(scored)} 条关系，{len(annotations.CURATED_COMPANIES)} 家公司")
    print(f"快照: {db_path}")
    print(f"派生数据: {derived_path}")
    print("\n评分预览（按置信度降序）：")
    for r in sorted(scored, key=lambda x: -x.confidence_score):
        print(f"  {r.confidence_score:3d}  {r.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
