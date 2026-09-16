"""人工标注核验层（Q10 核心质量环节）。

本文件是人工核验后的标注结果：每条关系的类型、方向、状态、量化等级、
不确定性备注均由作者依据采集到的 SEC 原文逐一判断（核验方式见 README
"AI 与工具使用声明"）。采集脚本将本层标注与自动抽取的证据组合生成快照。

方向语义（自 NVIDIA 视角）：
- object_to_nvidia     对方向 NVIDIA 供应产品/服务（supplier）
- nvidia_to_object     NVIDIA 向对方销售产品（customer）/ NVIDIA 投资对方
- mutual               双向/并行关系（partner / peer）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import (
    Company,
    Direction,
    Quantification,
    RelationshipStatus,
    RelationshipType,
)

# ---------------------------------------------------------------------------
# 公司画像（exchange/cik 会由采集脚本对 SEC 注册公司用官方 submissions API 校准）
# ---------------------------------------------------------------------------
CURATED_COMPANIES: dict[str, Company] = {
    "NVDA": Company(ticker="NVDA", name="NVIDIA Corporation", exchange="Nasdaq", security_id="SEC-CIK-0001045810", cik="0001045810", is_public=True, role_in_ecosystem="研究主体：加速计算平台（GPU/AI 芯片）设计商"),
    "TSM": Company(ticker="TSM", name="Taiwan Semiconductor Manufacturing Company Limited", exchange="NYSE", security_id="SEC-CIK-0001046179", cik="0001046179", is_public=True, role_in_ecosystem="晶圆代工厂（NVIDIA 先进制程主要代工方）"),
    "005930.KS": Company(ticker="005930.KS", name="Samsung Electronics Co., Ltd.", exchange="KRX (KOSPI)", security_id="KRX-005930", cik=None, is_public=True, role_in_ecosystem="晶圆代工 + 存储供应商；亦为 SoC 竞争对手"),
    "000660.KS": Company(ticker="000660.KS", name="SK hynix Inc.", exchange="KRX (KOSPI)", security_id="KRX-000660", cik=None, is_public=True, role_in_ecosystem="HBM 高带宽存储供应商"),
    "MU": Company(ticker="MU", name="Micron Technology, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0000723125", cik="0000723125", is_public=True, role_in_ecosystem="存储供应商（HBM/DRAM）"),
    "2317.TW": Company(ticker="2317.TW", name="Hon Hai Precision Industry Co., Ltd. (Foxconn)", exchange="TWSE", security_id="TWSE-2317", cik=None, is_public=True, role_in_ecosystem="电子代工厂（NVIDIA 产品组装/测试/封装）"),
    "3231.TW": Company(ticker="3231.TW", name="Wistron Corporation", exchange="TWSE", security_id="TWSE-3231", cik=None, is_public=True, role_in_ecosystem="电子代工厂（组装/测试/封装）"),
    "FN": Company(ticker="FN", name="Fabrinet", exchange="NYSE", security_id="SEC-CIK-0001408710", cik="0001408710", is_public=True, role_in_ecosystem="精密光学/电子代工厂"),
    "AMD": Company(ticker="AMD", name="Advanced Micro Devices, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0000002488", cik="0000002488", is_public=True, role_in_ecosystem="GPU/CPU/AI 加速器竞争对手"),
    "INTC": Company(ticker="INTC", name="Intel Corporation", exchange="Nasdaq", security_id="SEC-CIK-0000050863", cik="0000050863", is_public=True, role_in_ecosystem="CPU/GPU/AI 加速器竞争对手"),
    "AVGO": Company(ticker="AVGO", name="Broadcom Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001730168", cik="0001730168", is_public=True, role_in_ecosystem="网络芯片/定制 ASIC 竞争对手"),
    "QCOM": Company(ticker="QCOM", name="QUALCOMM Incorporated", exchange="Nasdaq", security_id="SEC-CIK-0000804328", cik="0000804328", is_public=True, role_in_ecosystem="SoC/Arm 生态竞争对手"),
    "MRVL": Company(ticker="MRVL", name="Marvell Technology, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001835632", cik="0001835632", is_public=True, role_in_ecosystem="网络/DPU/定制芯片竞争对手"),
    "CSCO": Company(ticker="CSCO", name="Cisco Systems, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0000858877", cik="0000858877", is_public=True, role_in_ecosystem="网络设备商：竞争（以太网交换）+ 合作（企业 AI 网络）"),
    "ANET": Company(ticker="ANET", name="Arista Networks, Inc.", exchange="NYSE", security_id="SEC-CIK-0001596532", cik="0001596532", is_public=True, role_in_ecosystem="数据中心网络竞争对手"),
    "HPE": Company(ticker="HPE", name="Hewlett Packard Enterprise Company", exchange="NYSE", security_id="SEC-CIK-0001645590", cik="0001645590", is_public=True, role_in_ecosystem="服务器厂商：竞争（网络）+ 合作（AI 服务器集成 NVIDIA 方案）"),
    "MSFT": Company(ticker="MSFT", name="Microsoft Corporation", exchange="Nasdaq", security_id="SEC-CIK-0000789019", cik="0000789019", is_public=True, role_in_ecosystem="云厂商（Azure）：自研 AI 芯片竞争，同时为重要（间接）客户"),
    "AMZN": Company(ticker="AMZN", name="Amazon.com, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001018724", cik="0001018724", is_public=True, role_in_ecosystem="云厂商（AWS）：自研芯片竞争，同时为潜在大客户"),
    "GOOGL": Company(ticker="GOOGL", name="Alphabet Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001652044", cik="0001652044", is_public=True, role_in_ecosystem="云厂商（GCP/TPU）：竞争，同时为潜在大客户"),
    "TSLA": Company(ticker="TSLA", name="Tesla, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001318605", cik="0001318605", is_public=True, role_in_ecosystem="自研 SoC 的车企（自动驾驶芯片自研）"),
    "HUAWEI": Company(ticker="HUAWEI", name="Huawei Technologies Co., Ltd.", exchange=None, security_id=None, cik=None, is_public=False, role_in_ecosystem="非上市竞争对手（云/AI 芯片/Arm CPU）"),
    "AMBA": Company(ticker="AMBA", name="Ambarella, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001280263", cik="0001280263", is_public=True, role_in_ecosystem="边缘 AI SoC 竞争对手"),
    "LITE": Company(ticker="LITE", name="Lumentum Holdings Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001633978", cik="0001633978", is_public=True, role_in_ecosystem="光模块/光器件竞争对手"),
    "OPENAI": Company(ticker="OPENAI", name="OpenAI", exchange=None, security_id=None, cik=None, is_public=False, role_in_ecosystem="AI 模型公司；NVIDIA 拟投资与深度合作对象"),
    "CRWV": Company(ticker="CRWV", name="CoreWeave, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001769628", cik="0001769628", is_public=True, role_in_ecosystem="AI 云（GPU 云）运营商；NVIDIA 直接客户"),
    "SMCI": Company(ticker="SMCI", name="Super Micro Computer, Inc.", exchange="Nasdaq", security_id="SEC-CIK-0001375365", cik="0001375365", is_public=True, role_in_ecosystem="AI 服务器厂商；NVIDIA 客户/合作伙伴"),
}


# ---------------------------------------------------------------------------
# 人工标注的关系
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CuratedRelation:
    object_ticker: str
    relationship_type: RelationshipType
    direction: Direction
    status: RelationshipStatus
    quantification: Quantification
    role_note: str  # 关系内容一句话描述（用于 README / 图谱说明）
    uncertainty_notes: str | None = None
    # 在 NVDA 10-K 中定位该实体的检索词（产生 sec_filing 级证据）
    nvda_10k_terms: tuple[str, ...] = ()
    # 上下游交叉验证：对 方 CIK（其 SEC 年报提及 NVIDIA 的最新文件）
    cross_cik: str | None = None
    cross_forms: str = "10-K"
    cross_terms: tuple[str, ...] = ("NVIDIA",)  # 在对方文件中定位的检索词
    cross_min_date: date = date(2023, 1, 1)  # 过旧的提及不作为交叉验证证据


_SUPPLIER_NOTE_DUAL = "NVIDIA FY2026 10-K 同时将其列为供应商与竞争对手（多重角色）"
_CUSTOMER_CONCENTRATION_NOTE = (
    "NVIDIA FY2026 10-K 披露前两大直接客户合计占收入 36%（22%+14%）但未点名，"
    "大型云厂商/系统集成商可能同时是竞争对手与客户"
)

CURATED_RELATIONS: list[CuratedRelation] = [
    # ---------------- 供应商（NVDA 10-K 直接点名，fact） ----------------
    CuratedRelation(
        object_ticker="TSM", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="晶圆代工：NVIDIA GPU/加速器晶圆的主要制造方",
        uncertainty_notes="NVIDIA 10-K 未披露对 TSMC 的采购占比；先进制程供应高度集中属公司披露的重大风险。TSMC 自身 20-F 未点名 NVIDIA，交叉验证证据缺失（待补）。",
        nvda_10k_terms=("TSMC", "Taiwan Semiconductor"),
        cross_cik="0001046179", cross_forms="20-F",
    ),
    CuratedRelation(
        object_ticker="005930.KS", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="晶圆代工 + 存储（HBM）双角色供应商",
        uncertainty_notes=_SUPPLIER_NOTE_DUAL + "；Samsung 在韩国上市，无 SEC 年报，交叉验证依赖 NVIDIA 单方披露。",
        nvda_10k_terms=("Samsung",),
    ),
    CuratedRelation(
        object_ticker="000660.KS", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="HBM 高带宽存储主要供应商",
        uncertainty_notes="SK hynix 在韩国上市，无 SEC 年报，交叉验证依赖 NVIDIA 单方披露；10-K 将其拼写为 'SK Hynix Inc.'。",
        nvda_10k_terms=("SK Hynix",),
    ),
    CuratedRelation(
        object_ticker="MU", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="存储（HBM/DRAM）供应商",
        uncertainty_notes="Micron 近年年报中未见对 NVIDIA 的点名披露（EDGAR 全文检索确认），关系证据目前仅来自 NVIDIA 单方 10-K。",
        nvda_10k_terms=("Micron",),
        cross_cik="0000723125",
    ),
    CuratedRelation(
        object_ticker="2317.TW", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="电子代工厂：NVIDIA 产品组装/测试/封装（contract manufacturer）",
        uncertainty_notes="Hon Hai 在台湾上市，无 SEC 年报，交叉验证依赖 NVIDIA 单方披露。",
        nvda_10k_terms=("Hon Hai",),
    ),
    CuratedRelation(
        object_ticker="3231.TW", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="电子代工厂：组装/测试/封装（contract manufacturer）",
        uncertainty_notes="Wistron 在台湾上市，无 SEC 年报，交叉验证依赖 NVIDIA 单方披露。",
        nvda_10k_terms=("Wistron",),
    ),
    CuratedRelation(
        object_ticker="FN", relationship_type=RelationshipType.supplier,
        direction=Direction.object_to_nvidia, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="独立分包商/代工厂：组装/测试/封装",
        uncertainty_notes="Fabrinet 自身 10-K 亦提及 NVIDIA（见交叉验证证据），但其披露口径为业务合作而非点名供应关系。",
        nvda_10k_terms=("Fabrinet",),
        cross_cik="0001408710",
    ),
    # ---------------- 同业竞争对手（NVDA 10-K Competition 节点名，fact） ----------------
    CuratedRelation(
        object_ticker="AMD", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="AI 加速器/GPU/CPU 全面对手（MI 系列 vs. NVDA 数据中心 GPU）",
        nvda_10k_terms=("AMD", "Advanced Micro"),
        cross_cik="0000002488",
    ),
    CuratedRelation(
        object_ticker="INTC", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="CPU/GPU/AI 加速器竞争对手",
        nvda_10k_terms=("Intel",),
        cross_cik="0000050863",
    ),
    CuratedRelation(
        object_ticker="AVGO", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="网络交换芯片/定制 ASIC（含云厂商自研加速器代工设计）竞争对手",
        nvda_10k_terms=("Broadcom",),
        cross_cik="0001730168",
    ),
    CuratedRelation(
        object_ticker="QCOM", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="SoC/Arm 生态竞争对手（汽车/边缘/终端 AI）",
        nvda_10k_terms=("Qualcomm",),
        cross_cik="0000804328",
    ),
    CuratedRelation(
        object_ticker="MRVL", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="网络/互连/DPU 及定制芯片竞争对手",
        nvda_10k_terms=("Marvell",),
        cross_cik="0001835632",
    ),
    CuratedRelation(
        object_ticker="ANET", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="数据中心以太网网络竞争对手（_vs. NVIDIA Spectrum/NVLink 生态）",
        nvda_10k_terms=("Arista",),
        cross_cik="0001596532",
    ),
    CuratedRelation(
        object_ticker="TSLA", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="自研 SoC（自动驾驶芯片）的整车厂，属 NVIDIA SoC 竞争类别",
        nvda_10k_terms=("Tesla",),
        cross_cik="0001318605",
    ),
    CuratedRelation(
        object_ticker="HUAWEI", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="非上市竞争对手（云/AI 芯片/Arm CPU 多领域）",
        uncertainty_notes="Huawei 非上市、无公开证券标识与 SEC 文件，无法交叉验证。",
        nvda_10k_terms=("Huawei",),
    ),
    CuratedRelation(
        object_ticker="AMBA", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="边缘 AI/视觉 SoC 竞争对手",
        nvda_10k_terms=("Ambarella",),
        cross_cik="001280263",
    ),
    CuratedRelation(
        object_ticker="LITE", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="光模块/光器件竞争对手（AI 数据中心互连）",
        nvda_10k_terms=("Lumentum",),
        cross_cik="001633978",
    ),
    CuratedRelation(
        object_ticker="MSFT", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="云厂商（Azure）：自研 AI 加速芯片（Maia）的竞争对手；同时是 NVIDIA 生态重要间接客户",
        uncertainty_notes=_CUSTOMER_CONCENTRATION_NOTE,
        nvda_10k_terms=("Microsoft",),
        cross_cik="0000789019",
    ),
    CuratedRelation(
        object_ticker="AMZN", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="云厂商（AWS）：自研 Trainium/Inferentia 芯片竞争对手；同时是 NVIDIA 生态潜在大客户",
        uncertainty_notes=_CUSTOMER_CONCENTRATION_NOTE,
        nvda_10k_terms=("Amazon",),
        cross_cik="0001018724",
    ),
    CuratedRelation(
        object_ticker="GOOGL", relationship_type=RelationshipType.peer,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="云厂商（GCP/TPU）：自研 TPU 的竞争对手；同时是 NVIDIA 生态潜在大客户",
        uncertainty_notes=_CUSTOMER_CONCENTRATION_NOTE,
        nvda_10k_terms=("Alphabet",),
        cross_cik="0001652044",
    ),
    # ---------------- 合作伙伴（对方年报点名 NVIDIA，fact） ----------------
    CuratedRelation(
        object_ticker="CSCO", relationship_type=RelationshipType.partner,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="企业 AI 网络（Cisco Nexus/UCS 集成 NVIDIA GPU/BlueField）合作伙伴；同时在以太网交换市场竞争",
        uncertainty_notes="竞争与合作并存：NVIDIA 10-K 将 Cisco 列入网络产品竞争对手，Cisco 年报亦披露与 NVIDIA 的合作。",
        nvda_10k_terms=("Cisco",),
        cross_cik="0000858877",
    ),
    CuratedRelation(
        object_ticker="HPE", relationship_type=RelationshipType.partner,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="AI 服务器/私有云（HPE 与 NVIDIA 联合方案）合作伙伴；同时在网络市场竞争",
        uncertainty_notes="竞争与合作并存：NVIDIA 10-K 将 HPE 列入网络产品竞争对手。",
        nvda_10k_terms=("Hewlett Packard",),
        cross_cik="0001645590",
    ),
    # ---------------- 投资与被投资（NVDA 10-K 点名，fact） ----------------
    CuratedRelation(
        object_ticker="OPENAI", relationship_type=RelationshipType.investor_or_investee,
        direction=Direction.nvidia_to_object, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="NVIDIA 拟对 OpenAI 进行投资并建立合作（AI 基础设施生态）",
        uncertainty_notes="NVIDIA FY2026 10-K 明确披露'正在敲定(finalizing)与 OpenAI 的投资与合作协议'，但同时声明交易可能无法达成——截至研究截点协议尚未最终签署。",
        nvda_10k_terms=("OpenAI",),
    ),
    # ---------------- 客户（对方年报点名 NVIDIA，fact/inference） ----------------
    CuratedRelation(
        object_ticker="CRWV", relationship_type=RelationshipType.customer,
        direction=Direction.nvidia_to_object, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="GPU 云运营商：CoreWeave 部署 NVIDIA GB200/GB300 NVL72 等系统，为 NVIDIA GPU 核心直接客户",
        uncertainty_notes="关系方向由 CoreWeave 披露口径确认（其业务大规模部署 NVIDIA 硬件）；NVIDIA 10-K 未点名该客户；CoreWeave 10-K 未在对 NVIDIA 义务中披露具体采购金额（定性口径）。",
        cross_cik="0001769628",
        cross_terms=("GB200",),
    ),
    # ---------------- 投资（对方 10-K 后续事项点名，fact + 量化） ----------------
    CuratedRelation(
        object_ticker="CRWV", relationship_type=RelationshipType.investor_or_investee,
        direction=Direction.nvidia_to_object, status=RelationshipStatus.fact,
        quantification=Quantification.quantified,
        role_note="NVIDIA 于 2026 年 1 月以 $87.20/股 投资 20 亿美元入股 CoreWeave（A 类普通股）",
        uncertainty_notes="金额与价格来自 CoreWeave 10-K 后续事项(Subsequent Events)披露（2026-01）；系少数股权投资，非收购。",
        cross_cik="0001769628",
        cross_terms=("NVIDIA Corporation invested",),
    ),
    CuratedRelation(
        object_ticker="CRWV", relationship_type=RelationshipType.partner,
        direction=Direction.mutual, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="2026 年 1 月 CoreWeave 与 NVIDIA 签署 collaboration framework，扩展长期互补合作以推进全球 AI 应用",
        uncertainty_notes="协议细节（金额/期限）未在 10-K 中披露。",
        cross_cik="0001769628",
        cross_terms=("collaboration framework",),
    ),
    CuratedRelation(
        object_ticker="SMCI", relationship_type=RelationshipType.customer,
        direction=Direction.nvidia_to_object, status=RelationshipStatus.fact,
        quantification=Quantification.qualitative,
        role_note="AI 服务器厂商：基于 NVIDIA GPU 构建服务器系统的直接客户/合作伙伴",
        uncertainty_notes="关系方向由 Super Micro 披露口径确认（其产品深度依赖 NVIDIA GPU 供应）；NVIDIA 10-K 未点名该客户。",
        cross_cik="0001375365",
    ),
]
