"""Explainable scoring engine (Q5).

Pure, deterministic function: no IO, no randomness, unit-testable.

    confidence = 100 x (w_cred*C + w_indep*I + w_time*T + w_type*R + w_quant*Q)

Factor rules (weights publicly declared in config.WEIGHTS):
  C 证据可信度  SEC 文件 1.0 / 官方新闻稿 0.8 / 权威媒体 0.6 / 其他 0.4
                —— 按证据 source_url 域名确定性分类，取全部证据中的最高档
  I 独立性      独立信源数：1 源 0.4，每多 1 源 +0.3，≥3 源 1.0（线性插值）
  T 时效性      证据 published_at 距 as_of ≤1 年 1.0，每多 1 年扣 0.2，下限 0；
                as_of 早于证据（未来证据）该证据计 0；取全部证据中的最优值
  R 关系类型强度 10-K/SEC 点名 1.0 / 官宣合作 0.8 / 投资 0.7 / 同业 0.6 / 推测 0.3
  Q 可量化      披露金额或占比 1.0 / 定性描述 0.5 / 无 0.2

无证据的关系自动降级：C=I=T=0，仅 R/Q 参与（总分上限 30）。
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from . import config
from .models import (
    Direction,
    Evidence,
    FactorDetail,
    Quantification,
    Relationship,
    RelationshipInput,
    RelationshipStatus,
    RelationshipType,
    ScoreBreakdown,
)

# ---------------------------------------------------------------------------
# 来源分类（确定性：基于域名 / 发布方）
# ---------------------------------------------------------------------------
SEC_DOMAINS = {"sec.gov"}

OFFICIAL_DOMAINS = {
    # NVIDIA 及常见上下游公司官方域名（IR / 新闻稿页面）
    "nvidia.com", "nvidianews.nvidia.com",
    "tsmc.com", "pr.tsmc.com",
    "skhynix.com", "news.skhynix.com",
    "samsung.com", "news.samsung.com",
    "amd.com", "ir.amd.com",
    "broadcom.com", "marvell.com", "micron.com",
    "foxconn.com", "honhai.com",
    "dell.com", "hpe.com", "supermicro.com", "lenovo.com",
    "quanta.com.cn", "quantaq.com", "wistron.com", "pegatroncorp.com", "inventec.com",
}

MEDIA_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "nikkei.com",
    "cnbc.com", "apnews.com", "marketwatch.com", "barrons.com",
    "investorplace.com", "seekingalpha.com",
}


def _domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        host = ""
    return host.lower().removeprefix("www.")


def _matches(domain: str, domains: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def classify_credibility(evidence: Evidence) -> str:
    """Return credibility tier key for one evidence item (deterministic)."""
    domain = _domain(evidence.source_url)
    if _matches(domain, SEC_DOMAINS) or "sec.gov" in evidence.source_url:
        return "sec_filing"
    if _matches(domain, OFFICIAL_DOMAINS):
        return "official_press_release"
    if _matches(domain, MEDIA_DOMAINS):
        return "reputable_media"
    return "other"


# ---------------------------------------------------------------------------
# 单因子计算
# ---------------------------------------------------------------------------
def factor_credibility(evidence: list[Evidence]) -> tuple[float, str]:
    if not evidence:
        return 0.0, "无证据，证据可信度无法评估（记 0）"
    tier_values = {config.CREDIBILITY_TIERS[classify_credibility(e)] for e in evidence}
    value = max(tier_values)
    tiers = "/".join(
        sorted({classify_credibility(e) for e in evidence})
    )
    return value, f"全部证据来源分档中的最高档（来源分档：{tiers}）"


def factor_independence(evidence: list[Evidence]) -> tuple[float, str]:
    """独立信源按发布方（披露主体）去重：NVIDIA 的 10-K 与 AMD 的 10-K
    虽同在 sec.gov 域名下，但属独立披露主体，计为独立信源。"""
    if not evidence:
        return 0.0, "无证据，独立性无法评估（记 0）"
    publishers = {(e.publisher.strip().lower() or _domain(e.source_url)) for e in evidence}
    n = len(publishers)
    value = min(
        1.0,
        config.INDEPENDENCE_SINGLE_SOURCE + (n - 1) * config.INDEPENDENCE_STEP,
    )
    return value, f"独立信源（按披露主体去重）共 {n} 个"


def _evidence_timeliness(evidence: Evidence, as_of: date) -> tuple[float, str]:
    if evidence.published_at is None:
        return 0.5, "证据无发布日期，按中性 0.5 处理"
    if evidence.published_at > as_of:
        return 0.0, "证据发布日期晚于研究截点 as_of（未来证据），时效性记 0"
    age_years = (as_of - evidence.published_at).days / 365.25
    if age_years <= config.TIMELINESS_FULL_YEARS:
        return 1.0, f"证据距今 {age_years:.1f} 年（≤1 年，时效性满分）"
    penalty = config.TIMELINESS_DECAY_PER_YEAR * (age_years - config.TIMELINESS_FULL_YEARS)
    value = max(0.0, 1.0 - penalty)
    return value, f"证据距今 {age_years:.1f} 年，按每年扣 {config.TIMELINESS_DECAY_PER_YEAR} 衰减"


def factor_timeliness(evidence: list[Evidence], as_of: date) -> tuple[float, str]:
    if not evidence:
        return 0.0, "无证据，时效性无法评估（记 0）"
    scored = [_evidence_timeliness(e, as_of) for e in evidence]
    best_value, best_note = max(scored, key=lambda t: t[0])
    return best_value, f"取最新有效证据的时效性：{best_note}"


def relation_strength_basis(rel: RelationshipInput) -> tuple[float, str]:
    """R 因子：关系确定度（10-K 点名 1.0 / 官宣 0.8 / 投资 0.7 / 同业 0.6 / 推测 0.3）。"""
    if rel.status == RelationshipStatus.inference or rel.status == RelationshipStatus.unknown:
        return config.RELATION_STRENGTH["speculative"], (
            f"关系状态为 {rel.status.value}（非直接事实），按推测档计"
        )
    if rel.relationship_type == RelationshipType.peer:
        return config.RELATION_STRENGTH["industry_peer"], "同业关系，按同业档计"
    if rel.relationship_type == RelationshipType.investor_or_investee:
        return config.RELATION_STRENGTH["investment"], "投资关系，按投资档计"
    if any(
        "sec.gov" in e.source_url or _domain(e.source_url) in SEC_DOMAINS
        for e in rel.evidence
    ):
        return config.RELATION_STRENGTH["sec_named"], "SEC 文件中点名的关系，按 10-K/SEC 点名档计"
    return config.RELATION_STRENGTH["official_announcement"], "官方渠道宣布的关系，按官宣合作档计"


def factor_quantification(rel: RelationshipInput) -> tuple[float, str]:
    value = config.QUANTIFICATION[rel.quantification.value]
    labels = {
        "quantified": "披露了金额或占比等量化数据",
        "qualitative": "仅有定性描述",
        "none": "无量化信息",
    }
    return value, labels[rel.quantification.value]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def score_relationship(
    rel: RelationshipInput, as_of: date | None = None
) -> Relationship:
    """Score one relationship deterministically and return the full record."""
    as_of = as_of or rel.as_of or config.AS_OF

    factors: list[tuple[str, float, str]] = [
        ("credibility", *factor_credibility(rel.evidence)),
        ("independence", *factor_independence(rel.evidence)),
        ("timeliness", *factor_timeliness(rel.evidence, as_of)),
        ("relation_strength", *relation_strength_basis(rel)),
        ("quantifiability", *factor_quantification(rel)),
    ]

    details: dict[str, FactorDetail] = {}
    total = 0.0
    for name, value, note in factors:
        weight = config.WEIGHTS[name]
        contribution = weight * value
        total += contribution
        details[name] = FactorDetail(
            value=round(value, 4),
            weight=weight,
            contribution=round(contribution, 4),
            explanation=note,
        )

    final = max(0, min(100, round(100 * total)))

    return Relationship(
        id=rel.id,
        subject=rel.subject,
        object=rel.object,
        relationship_type=rel.relationship_type,
        direction=rel.direction,
        status=rel.status,
        as_of=as_of,
        quantification=rel.quantification,
        confidence_score=final,
        score_breakdown=ScoreBreakdown(**details, total=final),
        evidence=rel.evidence,
        uncertainty_notes=rel.uncertainty_notes,
    )
