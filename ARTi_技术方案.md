# ARTi 供应链与合作关系研究挑战 — 技术方案

> 目标：选择一个研究对象（NVIDIA 或 宇树科技），基于**合法可访问的公开资料**，构建一个**可复现**的供应链与合作关系研究服务，将"关系结论 + 证据 + 时效 + 方向 + 可解释评分"连接起来，让 reviewer 能理解、运行、追溯。

---

## 1. 研究对象选择

**推荐：NVIDIA（NVDA · 纳斯达克）**

| 维度 | NVIDIA | 宇树科技 |
| --- | --- | --- |
| 是否上市 | 是（NVDA:NASDAQ） | **是（已上市，交易所与代码以实际为准）** |
| 证券标识 | 明确 | 明确（上市后可获得） |
| 自有披露 | 10-K/10-Q 直接披露主要客户/供应商 | 上市后按要求披露，覆盖度低于 NVIDIA |
| 上下游数据 | 上下游多为上市公司，公开数据极丰富 | 上市后披露增加，但供应链公开数据仍少于 NVIDIA |
| 难度 | 中（数据多但需去噪） | 中高（数据较 NVIDIA 稀疏，推断占比更大） |

选 NVIDIA 的原因：两者均为上市公司、都满足 Q1 的"证券标识"要求；但 NVIDIA 的 10-K 直接点名主要供应商/客户（如 TSMC、SK Hynix），上下游又多为上市公司 → 更贴合 Q2/Q4 对"关联上市公司"的硬要求；且 SEC EDGAR 提供免费、合法、结构化（XBRL）、允许抓取的官方数据 → 天然满足 Q3 的"合法可访问公开资料"。若选宇树科技，需在其上市后披露与公开新闻基础上补充反推，工作量更大。

> 注：本方案撰写时已确认宇树科技完成上市；具体交易所、股票代码与证券标识请在 README 中据实填写，避免写死占位符。

---

## 2. 总体架构

```
公开数据源 ──▶ 采集层（守 robots / 限速）──▶ 关系抽取 + 人工标注 ──▶ 可解释评分引擎
                                                                     │
                                                                     ▼
                                            SQLite 快照（可复现，无需重抓）──▶ API + CLI ──▶ Reviewer
                                                     ▲                                              │
                                                     └──────── 人工核验回环（Q10）◀─────────────────┘
```

核心理念：**采集守规矩、关系可溯源、评分可解释、仓库自带快照**。

---

## 3. 数据源（全部合法、可访问，遵守 robots.txt 与限速）

| 来源 | 用途 | 合法性说明 |
| --- | --- | --- |
| SEC EDGAR（10-K/10-Q 全文 + XBRL 公司事实 + Full-Text Search API） | NVIDIA 自身及上下游披露 | 官方公开，需设置 `User-Agent`，遵守其速率指引 |
| NVIDIA Investor Relations 新闻稿 | 合作/投资/产品发布 | 官网公开 |
| 上下游上市公司各自 SEC 文件 / 官网（TSMC、SK Hynix、Samsung、AMD、Broadcom、Marvell…） | 交叉验证关系 | 公开 |
| 公开市场数据 yfinance / 公司官网 / Wikipedia | 行情、同业候选（peer） | 公开、限速使用 |
| Google News RSS | 公开新闻线索 | 公开、无需登录 |

**红线（Q3）：** 绝不绕过 robots / 登录 / 付费墙 / 验证码 / 限流；绝不提交密钥、个人数据、客户机密、受限原始数据。

---

## 4. 数据模型（核心实体）

**Relationship（关系记录）**
- `id`、`subject`（NVIDIA）、`object`（关联实体）
- `relationship_type` ∈ {supplier, customer, partner, investor_or_investee, peer}
- `direction`：NVIDIA→object / object→NVIDIA / mutual
- `status` ∈ {fact, inference, unknown}
- `as_of`：研究截点（时间戳）
- `confidence_score`（0–100）+ `score_breakdown`（分项明细）
- `evidence[]`：每条含 `source_url`、`publisher`、`published_at`、`accessed_at`、`evidence_locator`、`access_license`、`note`
- `uncertainty_notes`：冲突/歧义/过期处理

**Company（公司画像）**
- `ticker`、`name`、`exchange`、`security_id`、`is_public`、`role_in_ecosystem`

---

## 5. 评分引擎（Q5：可解释，0–100）

确定性加权模型，逐条输出分项与总分，保证可复现：

```
confidence = 100 × (w_cred·C + w_indep·I + w_time·T + w_type·R + w_quant·Q)
```

| 因子 | 含义 | 评分规则（示例） |
| --- | --- | --- |
| C 证据可信度 | 来源权威度 | SEC 文件 1.0 / 官方新闻稿 0.8 / 权威媒体 0.6 / 其他 0.4 |
| I 独立性 | 独立信源数 | 1 源 0.4，≥3 源 1.0（线性插值） |
| T 时效性 | 证据距 `as_of` 的衰减 | ≤1 年 1.0，每多 1 年扣 0.2 |
| R 关系类型强度 | 关系确定度 | 10-K 点名 1.0 / 官宣合作 0.8 / 投资 0.7 / 同业 0.6 / 推测 0.3 |
| Q 可量化 | 是否有披露数据 | 披露金额或占比 1.0 / 定性描述 0.5 / 无 0.2 |

- 权重在 `config.py` / README 中**公开声明**
- 每条 relationship 都返回 `score_breakdown`，解释"证据可信度、独立性、时效、关系类型、可量化信息"各自如何影响最终分
- 纯函数、确定性、可单测

---

## 6. API 与 CLI（Q7）

**FastAPI（HTTP JSON API）**
- `GET /api/companies/{ticker}` — 公司画像 + 评分明细
- `GET /api/relationships` — 过滤：`company`、`type`、`min_relevance`、`as_of`、`page`、`page_size`；支持分页
- `GET /api/relationships/{id}` — 单条关系 + evidence
- `GET /api/relationships/{id}/evidence`
- `GET /api/graph` — 返回 nodes/edges（供关系图渲染）

输入校验（pydantic）：非法 `type` / 分页参数 → `422` + 明确 message；未知 ticker → `404`；关系图空 → 明确空响应。

**CLI（Typer）**
```
artisearch query --type supplier --min-relevance 50 --as-of 2026-09
artisearch company NVDA
artisearch graph
```
本地即可运行，读 SQLite 快照，**无需联网**。

---

## 7. 复现与数据快照（Q6 / Q8）

- 仓库内置 `data/snapshot.sqlite`（或 JSON fixture）+ `data/raw/` 原始抓取存档 → reviewer 无需重新抓取即可运行
- `scripts/collect.py` 可重跑采集（守 robots/限速），但默认读快照
- README 含：依赖安装、无需真实凭据的环境变量说明、启动/测试命令、数据更新方式、复现步骤
- 提供 fixture / snapshot / 明确数据截点（满足 Q8）

---

## 8. 测试（Q9）

pytest（以 snapshot 为 Golden 数据）：
- **关键路径**：API 返回关系列表 / 单条 / 图；评分计算正确
- **失败 / 边界**：未知 ticker→404、非法 type→422、空证据关系→降级、as_of 早于证据→时效性归零
- README 写明限制、已知盲区（非上市供应商、保密客户、新闻偶发误判）、未来数据质量改进方向

---

## 9. AI / 工具使用声明（Q10）

README 增设"AI 与工具使用声明"段：
- 是否使用 LLM / 编程 Agent / 检索工具、用于哪些环节（如草稿生成、关系抽取辅助）
- 人工如何核验（逐条核对 `source_url` 与原文）
- 由作者个人对研究与工程判断负责
- 绝不在工具中输入密钥 / 个人数据 / 客户机密 / 未授权资料

---

## 10. 与验收清单逐条映射

| 验收项 | 本方案对应设计 |
| --- | --- |
| Q1 研究对象/实体/证券标识/时间戳/边界/事实-推断-未知/免责 | README 头部 + `Company` 模型 + `status` 字段 + 免责声明 |
| Q2 五类关系 + 方向/身份/状态/时效/不确定性 | `Relationship` 模型全字段覆盖 |
| Q3 合法公开资料 / 不绕过访问控制 / 不提交敏感数据 | 数据源表 + 红线 + 采集层 robots/限速 |
| Q4 证据溯源 + 处理歧义/冲突/过期/误判 | `evidence[]` 全字段 + `uncertainty_notes` |
| Q5 0–100 相关度/置信度 + 解释 | 第 5 节评分引擎 + `score_breakdown` |
| Q6 仓库说明数据源与采集/清洗 + 可复现 | `data/snapshot.sqlite` + `collect.py` |
| Q7 HTTP JSON API + CLI + 校验/筛选/分页/错误 | 第 6 节 |
| Q8 依赖/环境变量/启动测试/复现/fixture | README + `requirements.txt` + 快照 |
| Q9 关键路径 + 边界测试 + 限制/盲区 | 第 8 节 |
| Q10 AI/工具声明 + 人工核验 + 责任 | 第 9 节 |

---

## 11. 实施步骤（建议顺序）

1. 仓库骨架 + 虚拟环境 + 依赖（Python 3.11+ / FastAPI / Typer / pytest）
2. 数据模型 schema + SQLite 初始化
3. 采集脚本（SEC EDGAR 优先），守 robots/限速，落库
4. **人工标注与核验关系、证据**（核心质量环节，不可全交给工具）
5. 评分引擎 + 单元测试
6. FastAPI + CLI
7. pytest（关键路径 + 边界）
8. README 覆盖 Q1–Q10
9. 本地跑通 + 逐条自检
10. 推送 GitHub，登录填写简历 / 交付摘要 / 仓库链接

---

## 12. 建议目录结构

```
arti-supplychain/
├── README.md                # 覆盖 Q1–Q10
├── requirements.txt
├── pyproject.toml
├── artisearch/
│   ├── models.py            # 数据模型 / pydantic
│   ├── db.py                # SQLite 读写
│   ├── collect.py           # 合法采集（SEC EDGAR 等）
│   ├── score.py             # 可解释评分引擎
│   ├── api.py               # FastAPI
│   ├── cli.py               # Typer CLI
│   └── config.py            # 权重 / 环境变量
├── data/
│   ├── snapshot.sqlite      # 评审快照（可复现）
│   └── raw/                 # 原始抓取存档
├── tests/
│   ├── fixtures/snapshot.json
│   ├── test_api.py
│   └── test_score.py
└── scripts/
    └── collect.py
```

---

### 一句话总结
选 NVIDIA → 用 SEC EDGAR 等合法公开源采集 → 人工标注五类关系与证据 → 可解释加权评分（0–100）→ FastAPI + CLI 暴露 → 自带 SQLite 快照保证可复现 → README 逐条对齐 Q1–Q10 验收。
