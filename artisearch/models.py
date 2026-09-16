"""Pydantic data models (Q1/Q2/Q4): Company, Evidence, Relationship, scoring
breakdown, and API response models.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums (Q2: 五类关系 / 方向 / 状态)
# ---------------------------------------------------------------------------
class RelationshipType(str, Enum):
    supplier = "supplier"
    customer = "customer"
    partner = "partner"
    investor_or_investee = "investor_or_investee"
    peer = "peer"


class Direction(str, Enum):
    nvidia_to_object = "nvidia_to_object"
    object_to_nvidia = "object_to_nvidia"
    mutual = "mutual"


class RelationshipStatus(str, Enum):
    fact = "fact"
    inference = "inference"
    unknown = "unknown"


class Quantification(str, Enum):
    quantified = "quantified"
    qualitative = "qualitative"
    none = "none"


# ---------------------------------------------------------------------------
# Core entities (Q4: 证据溯源)
# ---------------------------------------------------------------------------
class Company(BaseModel):
    ticker: str
    name: str
    exchange: str | None = None
    security_id: str | None = None
    cik: str | None = None
    is_public: bool = True
    role_in_ecosystem: str | None = None


class Evidence(BaseModel):
    source_url: str
    publisher: str
    published_at: date | None = None
    accessed_at: date
    evidence_locator: str  # e.g. "10-K FY2025, Item 1, 'Concentration of ...'" or URL fragment
    access_license: str = "public"
    note: str | None = None


# ---------------------------------------------------------------------------
# Scoring (Q5: 可解释 0-100)
# ---------------------------------------------------------------------------
class FactorDetail(BaseModel):
    """单个因子的取值、权重、贡献与解释。"""

    value: float = Field(..., ge=0.0, le=1.0)
    weight: float
    contribution: float
    explanation: str


class ScoreBreakdown(BaseModel):
    credibility: FactorDetail
    independence: FactorDetail
    timeliness: FactorDetail
    relation_strength: FactorDetail
    quantifiability: FactorDetail
    total: int = Field(..., ge=0, le=100)


# ---------------------------------------------------------------------------
# Relationship — 评分后的完整关系记录
# ---------------------------------------------------------------------------
class Relationship(BaseModel):
    id: str
    subject: str  # 通常为 "NVDA"
    object: str  # 关联实体 ticker / 名称
    relationship_type: RelationshipType
    direction: Direction
    status: RelationshipStatus
    as_of: date
    quantification: Quantification = Quantification.qualitative
    confidence_score: int = Field(..., ge=0, le=100)
    score_breakdown: ScoreBreakdown
    evidence: list[Evidence] = Field(default_factory=list)
    uncertainty_notes: str | None = None


class RelationshipInput(BaseModel):
    """评分引擎的输入：尚未计算 confidence 的原始关系记录。"""

    id: str
    subject: str
    object: str
    relationship_type: RelationshipType
    direction: Direction
    status: RelationshipStatus
    as_of: date
    quantification: Quantification = Quantification.qualitative
    evidence: list[Evidence] = Field(default_factory=list)
    uncertainty_notes: str | None = None


# ---------------------------------------------------------------------------
# API response models (Q7)
# ---------------------------------------------------------------------------
class RelationshipPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[Relationship]


class CompanyNotFound(BaseModel):
    detail: str


class GraphNode(BaseModel):
    id: str
    label: str
    is_subject: bool = False


class GraphEdge(BaseModel):
    id: str
    source: str  # node id
    target: str  # node id
    relationship_type: RelationshipType
    direction: Direction
    confidence_score: int


class GraphResponse(BaseModel):
    as_of: date
    nodes: list[GraphNode]
    edges: list[GraphEdge]
