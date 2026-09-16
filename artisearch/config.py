"""Central configuration: scoring weights (publicly declared, Q5), research cut-off,
EDGAR access settings, and filesystem paths.

All values here are part of the reproducible research declaration.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOT_PATH = Path(os.environ.get("ARTISEARCH_SNAPSHOT", str(DATA_DIR / "snapshot.sqlite")))

# ---------------------------------------------------------------------------
# Research cut-off (Q1: 研究时间戳/截点)
# ---------------------------------------------------------------------------
AS_OF = date(2026, 9, 16)

# ---------------------------------------------------------------------------
# Scoring engine — publicly declared weights (Q5)
# confidence = 100 x (w_cred*C + w_indep*I + w_time*T + w_type*R + w_quant*Q)
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "credibility": 0.30,        # C 证据可信度
    "independence": 0.20,       # I 独立性
    "timeliness": 0.20,         # T 时效性
    "relation_strength": 0.20,  # R 关系类型强度
    "quantifiability": 0.10,    # Q 可量化
}

# C 证据可信度分档（按来源域名分类，见 score.classify_credibility）
CREDIBILITY_TIERS: dict[str, float] = {
    "sec_filing": 1.0,            # SEC 官方文件
    "official_press_release": 0.8,  # 公司官方新闻稿 / IR 页面
    "reputable_media": 0.6,       # 权威财经媒体
    "other": 0.4,                 # 其他公开来源
}

# I 独立性：1 源 0.4，每多 1 个独立源 +0.3，≥3 源封顶 1.0（线性插值）
INDEPENDENCE_SINGLE_SOURCE = 0.4
INDEPENDENCE_STEP = 0.3

# T 时效性：证据距 as_of ≤1 年 1.0，每多 1 年扣 0.2，下限 0
TIMELINESS_FULL_YEARS = 1.0
TIMELINESS_DECAY_PER_YEAR = 0.2

# R 关系类型强度（关系确定度）
RELATION_STRENGTH: dict[str, float] = {
    "sec_named": 1.0,              # 10-K/SEC 文件点名
    "official_announcement": 0.8,  # 官宣合作
    "investment": 0.7,             # 投资关系披露
    "industry_peer": 0.6,          # 同业
    "speculative": 0.3,            # 推测
}

# Q 可量化
QUANTIFICATION: dict[str, float] = {
    "quantified": 1.0,    # 披露金额或占比
    "qualitative": 0.5,   # 定性描述
    "none": 0.2,          # 无
}

# ---------------------------------------------------------------------------
# SEC EDGAR access — 合规采集参数 (Q3)
# SEC 要求声明可识别身份的 User-Agent（姓名 + 邮箱格式），可用环境变量覆盖。
# ---------------------------------------------------------------------------
EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "ARTi Research Bot arti-research@example.com"
)
EDGAR_REQUEST_INTERVAL_SECONDS = 0.5  # 全局请求间隔 ≥0.5s（限速）
EDGAR_MAX_RETRIES = 3                 # 仅对 5xx/网络错误退避重试；429/403 立即停止
EDGAR_HTTP_TIMEOUT = 30.0

DATA_SEC_GOV = "https://data.sec.gov"
EFTS_SEC_GOV = "https://efts.sec.gov/LATEST/search-index"

# NVIDIA CIK
NVDA_CIK = "0001045810"
NVDA_TICKER = "NVDA"
