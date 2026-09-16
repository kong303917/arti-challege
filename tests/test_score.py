"""Unit tests for the explainable scoring engine (Q5 / Q9 边界)."""

from __future__ import annotations

from datetime import date

import pytest

from artisearch import config, score
from artisearch.models import (
    Direction,
    Evidence,
    Quantification,
    RelationshipInput,
    RelationshipStatus,
    RelationshipType,
)

AS_OF = date(2026, 9, 16)


def make_evidence(
    url: str = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000029/nvda-20260131.htm",
    publisher: str = "SEC EDGAR",
    published_at: date | None = date(2026, 2, 26),
    accessed_at: date = AS_OF,
) -> Evidence:
    return Evidence(
        source_url=url,
        publisher=publisher,
        published_at=published_at,
        accessed_at=accessed_at,
        evidence_locator="10-K FY2026, Item 1",
    )


def make_rel(
    rel_type: RelationshipType = RelationshipType.supplier,
    status: RelationshipStatus = RelationshipStatus.fact,
    evidence: list[Evidence] | None = None,
    quantification: Quantification = Quantification.quantified,
) -> RelationshipInput:
    return RelationshipInput(
        id="rel-test",
        subject="NVDA",
        object="TSM",
        relationship_type=rel_type,
        direction=Direction.object_to_nvidia,
        status=status,
        as_of=AS_OF,
        quantification=quantification,
        evidence=evidence if evidence is not None else [make_evidence()],
    )


# ---------------------------------------------------------------------------
# classify_credibility
# ---------------------------------------------------------------------------
class TestClassifyCredibility:
    def test_sec(self):
        assert score.classify_credibility(make_evidence()) == "sec_filing"

    def test_official_press_release(self):
        ev = make_evidence(url="https://nvidianews.nvidia.com/news/x")
        assert score.classify_credibility(ev) == "official_press_release"

    def test_reputable_media(self):
        ev = make_evidence(url="https://www.reuters.com/article/x")
        assert score.classify_credibility(ev) == "reputable_media"

    def test_other(self):
        ev = make_evidence(url="https://some-random-blog.example.com/post")
        assert score.classify_credibility(ev) == "other"


# ---------------------------------------------------------------------------
# Factor: credibility / independence
# ---------------------------------------------------------------------------
class TestCredibilityAndIndependence:
    def test_max_tier_across_evidence(self):
        ev = [
            make_evidence(url="https://blog.example.com/a"),
            make_evidence(url="https://www.reuters.com/b"),
        ]
        value, _ = score.factor_credibility(ev)
        assert value == pytest.approx(0.6)

    def test_single_source(self):
        value, _ = score.factor_independence([make_evidence()])
        assert value == pytest.approx(0.4)

    def test_two_sources_interpolated(self):
        ev = [
            make_evidence(publisher="NVIDIA Corp", url="https://www.sec.gov/a"),
            make_evidence(publisher="Reuters", url="https://www.reuters.com/b"),
        ]
        value, _ = score.factor_independence(ev)
        assert value == pytest.approx(0.7)

    def test_three_sources_capped_at_one(self):
        ev = [
            make_evidence(publisher="NVIDIA Corp", url="https://www.sec.gov/a"),
            make_evidence(publisher="Reuters", url="https://www.reuters.com/b"),
            make_evidence(publisher="Bloomberg", url="https://www.bloomberg.com/c"),
            make_evidence(publisher="FT", url="https://ft.com/d"),
        ]
        value, _ = score.factor_independence(ev)
        assert value == pytest.approx(1.0)

    def test_same_publisher_not_independent(self):
        ev = [
            make_evidence(publisher="NVIDIA 10-K"),
            make_evidence(publisher="NVIDIA 10-K"),
        ]
        value, _ = score.factor_independence(ev)
        assert value == pytest.approx(0.4)

    def test_different_publishers_on_sec_are_independent(self):
        """同一 sec.gov 域名下的不同披露主体计为独立信源。"""
        ev = [
            make_evidence(publisher="NVIDIA Corp 10-K"),
            make_evidence(publisher="Advanced Micro Devices 10-K"),
        ]
        value, _ = score.factor_independence(ev)
        assert value == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Factor: timeliness
# ---------------------------------------------------------------------------
class TestTimeliness:
    def test_within_one_year_full(self):
        value, _ = score.factor_timeliness([make_evidence(published_at=date(2026, 1, 1))], AS_OF)
        assert value == pytest.approx(1.0)

    def test_two_years_decayed(self):
        value, _ = score.factor_timeliness([make_evidence(published_at=date(2024, 9, 15))], AS_OF)
        assert value == pytest.approx(0.8, abs=0.01)

    def test_very_old_floor_zero(self):
        value, _ = score.factor_timeliness([make_evidence(published_at=date(2018, 1, 1))], AS_OF)
        assert value == pytest.approx(0.0)

    def test_evidence_after_as_of_zero(self):
        """边界：as_of 早于证据发布时间 → 时效性归零。"""
        value, _ = score.factor_timeliness(
            [make_evidence(published_at=date(2026, 12, 31))], AS_OF
        )
        assert value == 0.0

    def test_no_published_date_neutral(self):
        value, note = score.factor_timeliness([make_evidence(published_at=None)], AS_OF)
        assert value == pytest.approx(0.5)
        assert "无发布日期" in note


# ---------------------------------------------------------------------------
# Factor: relation strength
# ---------------------------------------------------------------------------
class TestRelationStrength:
    def test_fact_with_sec_evidence_sec_named(self):
        value, _ = score.relation_strength_basis(make_rel())
        assert value == pytest.approx(1.0)

    def test_fact_official_announcement(self):
        rel = make_rel(
            RelationshipType.partner,
            evidence=[make_evidence(url="https://nvidianews.nvidia.com/x")],
        )
        value, _ = score.relation_strength_basis(rel)
        assert value == pytest.approx(0.8)

    def test_investment(self):
        rel = make_rel(RelationshipType.investor_or_investee)
        value, _ = score.relation_strength_basis(rel)
        assert value == pytest.approx(0.7)

    def test_peer(self):
        rel = make_rel(RelationshipType.peer)
        value, _ = score.relation_strength_basis(rel)
        assert value == pytest.approx(0.6)

    def test_inference_speculative(self):
        rel = make_rel(status=RelationshipStatus.inference)
        value, _ = score.relation_strength_basis(rel)
        assert value == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Full scoring
# ---------------------------------------------------------------------------
class TestScoreRelationship:
    def test_perfect_score(self):
        """SEC 证据 + 3 独立披露主体 + 新近 + fact + 量化 → 满分 100。"""
        ev = [
            make_evidence(publisher="NVIDIA Corp", url="https://www.sec.gov/a", published_at=date(2026, 8, 1)),
            make_evidence(publisher="Reuters", url="https://www.reuters.com/b", published_at=date(2026, 8, 2)),
            make_evidence(publisher="Bloomberg", url="https://www.bloomberg.com/c", published_at=date(2026, 8, 3)),
        ]
        result = score.score_relationship(make_rel(evidence=ev), AS_OF)
        assert result.confidence_score == 100
        assert result.score_breakdown.credibility.value == pytest.approx(1.0)

    def test_weighted_total_math(self):
        """总分 = round(100 x Σ w·f)。"""
        result = score.score_relationship(
            make_rel(
                RelationshipType.peer,
                evidence=[make_evidence(url="https://www.reuters.com/x")],
                quantification=Quantification.qualitative,
            ),
            AS_OF,
        )
        b = result.score_breakdown
        expected = (
            b.credibility.contribution
            + b.independence.contribution
            + b.timeliness.contribution
            + b.relation_strength.contribution
            + b.quantifiability.contribution
        )
        assert result.confidence_score == round(100 * expected)

    def test_empty_evidence_degrades(self):
        """边界：空证据 → 降级（C/I/T=0，总分上限 = 100*(0.2+0.1)=30）。"""
        result = score.score_relationship(make_rel(evidence=[]), AS_OF)
        assert result.confidence_score <= 30
        assert result.score_breakdown.credibility.value == 0.0
        assert result.score_breakdown.independence.value == 0.0
        assert result.score_breakdown.timeliness.value == 0.0

    def test_all_future_evidence_timeliness_zero(self):
        ev = [
            make_evidence(published_at=date(2027, 1, 1)),
            make_evidence(published_at=date(2027, 6, 1)),
        ]
        result = score.score_relationship(make_rel(evidence=ev), AS_OF)
        assert result.score_breakdown.timeliness.value == 0.0

    def test_deterministic(self):
        rel = make_rel(
            RelationshipType.partner,
            evidence=[
                make_evidence(url="https://www.reuters.com/a", published_at=date(2025, 3, 1))
            ],
            quantification=Quantification.qualitative,
        )
        a = score.score_relationship(rel, AS_OF)
        b = score.score_relationship(rel, AS_OF)
        assert a.confidence_score == b.confidence_score
        assert a.score_breakdown.model_dump() == b.score_breakdown.model_dump()

    def test_breakdown_explanations_present(self):
        result = score.score_relationship(make_rel(), AS_OF)
        for field in (
            "credibility",
            "independence",
            "timeliness",
            "relation_strength",
            "quantifiability",
        ):
            assert getattr(result.score_breakdown, field).explanation
            assert getattr(result.score_breakdown, field).weight == config.WEIGHTS[field]

    def test_score_in_range(self):
        for status in RelationshipStatus:
            for q in Quantification:
                result = score.score_relationship(
                    make_rel(status=status, quantification=q, evidence=[]), AS_OF
                )
                assert 0 <= result.confidence_score <= 100
