# ARTi 供应链与合作关系研究 — NVIDIA (NVDA)

基于**合法可访问的公开资料**（以 SEC EDGAR 官方数据为主）构建的 NVIDIA 供应链与合作关系研究服务：
27 条关系、26 家公司、每条关系附带**可追溯证据链**与**可解释的 0-100 置信度评分**，通过 HTTP API 与 CLI 提供查询，仓库自带 SQLite 快照，**离线可复现**。

> **免责声明**：本项目为技术挑战作品，仅供研究学习用途，不构成投资建议。数据均来自公开渠道，截至研究截点 `2026-09-16`；关系判断可能存在错误或遗漏，作者对研究与工程判断负责（详见[AI 与工具使用声明](#10-ai-与工具使用声明)）。

---

## 1. 研究对象与边界（Q1）

| 项 | 值 |
| --- | --- |
| 研究主体 | NVIDIA Corporation |
| 证券标识 | `NVDA` · Nasdaq · SEC CIK 0001045810（快照中每家公司均含 `ticker`/`exchange`/`security_id`/`cik`） |
| 研究截点（as_of） | **2026-09-16**（每条关系记录均带 `as_of` 时间戳） |
| 研究边界 | 以 NVIDIA 为中心的一阶关系（供应商/客户/伙伴/投资/同业）；不展开二阶传导（如 TSMC 的供应商） |
| 事实/推断/未知 | 每条关系带 `status` ∈ `fact`（SEC 文件或双方披露直接证实）/ `inference`（间接推断）/ `unknown`（未验证） |

**方向语义**（自 NVIDIA 视角）：`object_to_nvidia` = 对方向 NVIDIA 供应；`nvidia_to_object` = NVIDIA 向对方销售或投资对方；`mutual` = 双向/并行关系。

## 2. 五类关系（Q2）

`relationship_type` ∈ `supplier` / `customer` / `partner` / `investor_or_investee` / `peer`，每条关系包含：方向、身份（公司画像：上市状态/交易所/证券标识/生态角色）、状态（fact/inference/unknown）、时效（as_of + 证据发布时间）、不确定性备注（`uncertainty_notes`，含冲突/歧义/过期说明，如 Samsung 同时是供应商与竞争对手、云厂商"竞争+客户"双重角色）。

快照构成（27 条）：supplier 7 · peer 13 · partner 3 · customer 2 · investor_or_investee 2，全部为 `fact`（由 SEC 文件直接证实）；`inference` / `unknown` 状态与降级路径在测试 fixture（`tests/fixtures/snapshot.json`）中有专门边界样本覆盖。

## 3. 数据源与采集合规（Q3 / Q6）

| 来源 | 用途 | 合规说明 |
| --- | --- | --- |
| [SEC EDGAR](https://www.sec.gov/edgar/) submissions API + 10-K/20-F 全文 | NVDA FY2026 10-K 供应商/竞争名单点名；上下游公司年报交叉验证 | 官方公开；请求携带声明式 `User-Agent`，全局限速 ≥0.5s/请求 |
| [EDGAR Full-Text Search API](https://efts.sec.gov/) | 定位对方年报中提及 NVIDIA 的最新文件 | 同上 |
| `company_tickers.json`（SEC 官方文件） | ticker→CIK 映射 | 公开 |

**红线遵守**：绝不绕过 robots / 登录 / 付费墙 / 验证码；遇 403/429（访问控制/限流）**立即终止**而非重试规避；未使用任何密钥、个人数据或受限原始数据。所有原始响应归档于 `data/raw/`（含 `manifest.jsonl` 访问日志）。

**采集流程**（`scripts/collect.py`，可重跑、幂等）：NVDA 最新 10-K → 按人工标注层（`artisearch/annotations.py`）定位实体提及并抽取证据片段 → EDGAR 全文搜索交叉验证（对方年报中的 NVIDIA 提及）→ 官方 submissions API 校准交易所信息 → 评分落库。

## 4. 证据溯源与不确定性处理（Q4）

每条证据（`evidence[]`）包含：`source_url`（SEC 原文链接）、`publisher`（披露主体）、`published_at`、`accessed_at`、`evidence_locator`（定位片段，含原文引用）、`access_license`、`note`。

歧义/冲突/过期处理示例（均记录在 `uncertainty_notes`）：
- **双重角色**：Samsung 同为代工供应商与 SoC 竞争对手；Cisco/HPE 同为合作伙伴与网络竞争对手；
- **未点名披露**：NVDA 10-K 披露前两大直接客户占收入 22%/14% 但未点名 → 云厂商客户关系标注为 peer+备注而非臆断 customer；
- **进行中交易**：对 OpenAI 的投资"正在敲定"，明确标注协议尚未签署；
- **交叉验证缺失**：SK hynix/Samsung/Hon Hai 等非 SEC 注册公司仅有 NVIDIA 单方披露，备注说明；
- **时效衰减**：过期证据由评分引擎时效因子自动降权（见下）。

## 5. 可解释评分引擎（Q5）

确定性加权模型（纯函数、可单测、无 IO）：

```
confidence = 100 × (0.30·C + 0.20·I + 0.20·T + 0.20·R + 0.10·Q)
```

| 因子 | 规则 | 说明 |
| --- | --- | --- |
| C 证据可信度 | SEC 文件 1.0 / 官方新闻稿 0.8 / 权威媒体 0.6 / 其他 0.4 | 按证据域名确定性分类，取最高档 |
| I 独立性 | 1 源 0.4，每多 1 个独立披露主体 +0.3，≥3 源 1.0 | 按披露主体去重（NVIDIA 10-K 与 AMD 10-K 计为独立信源） |
| T 时效性 | 距 as_of ≤1 年 1.0，每多 1 年 −0.2，下限 0 | as_of 早于证据发布日 → 该证据计 0 |
| R 关系类型强度 | SEC 点名 1.0 / 官宣 0.8 / 投资 0.7 / 同业 0.6 / 推断 0.3 | 由 status + 类型 + 证据来源确定性推导 |
| Q 可量化 | 披露金额/占比 1.0 / 定性 0.5 / 无 0.2 | 人工标注 |

权重在 `artisearch/config.py` 公开声明；每条关系返回 `score_breakdown`，含各因子取值、权重、贡献与中文解释（例：NVIDIA 对 CoreWeave 20 亿美元投资 → `quantified=1.0`，"披露了金额或占比等量化数据"）。**无证据的关系自动降级**（C/I/T=0，总分上限 30）。

## 6. 快速开始（Q8）

依赖：Python 3.12 + [uv](https://docs.astral.sh/uv/)。**无需任何真实凭据**；唯一环境变量 `EDGAR_USER_AGENT`（可选，重新采集时按 SEC 要求声明身份，默认为项目占位值）。

```bash
# 安装（uv 自动创建 .venv 并安装依赖）
uv sync

# 启动 API（读取仓库自带快照，离线可用）
uv run uvicorn artisearch.api:app --reload
# 交互式文档: http://127.0.0.1:8000/docs

# CLI（同样离线读快照）
uv run artisearch query --type supplier --min-relevance 50
uv run artisearch company NVDA
uv run artisearch graph
uv run artisearch query --company TSM --json

# 测试（64 项，含关键路径与边界）
uv run pytest
```

## 7. API（Q7）

| 端点 | 说明 |
| --- | --- |
| `GET /api/companies/{ticker}` | 公司画像（未知 ticker → 404） |
| `GET /api/relationships` | 过滤参数 `company` / `type` / `min_relevance` / `as_of` + 分页 `page` / `page_size`（非法值 → 422 + 明确 message） |
| `GET /api/relationships/{id}` | 单条关系 + 证据 + 评分明细 |
| `GET /api/relationships/{id}/evidence` | 单条关系证据链 |
| `GET /api/graph` | nodes/edges 关系图数据（空图返回明确空响应） |
| `GET /api/health` | 健康检查（返回 as_of 与快照路径） |

示例：

```bash
curl "http://127.0.0.1:8000/api/relationships?type=supplier&min_relevance=50&page=1&page_size=5"
curl "http://127.0.0.1:8000/api/relationships/NVDA-CRWV-investor_or_investee"
curl -i "http://127.0.0.1:8000/api/relationships?type=badtype"   # 422
curl -i "http://127.0.0.1:8000/api/companies/XXXX"               # 404
```

## 8. 数据更新与复现（Q6 / Q8）

```bash
# 重新采集生成快照（联网，遵守限速，全部原始响应归档 data/raw/）
uv run python scripts/collect.py

# 使用自定义输出位置
uv run python scripts/collect.py --db /tmp/snapshot.sqlite
```

- 仓库已内置 `data/snapshot.sqlite`（27 关系/26 公司）与 `data/raw/` 原始存档 → **评审无需联网与重抓**即可运行 API/CLI/测试；
- 采集幂等（重复运行覆盖重建），关系类型/方向/状态等判断固化在 `artisearch/annotations.py`（人工标注层）；
- 明确数据截点：`as_of = 2026-09-16`（`config.AS_OF`）。

## 9. 测试与已知限制（Q9）

`uv run pytest` — 64 项测试，以 `tests/fixtures/snapshot.json` 为 Golden 数据：

- **关键路径**：关系列表/过滤/分页、单条关系、证据链、图数据、公司画像、评分计算正确性与确定性；
- **边界**：未知 ticker → 404、非法 type/分页/日期 → 422、空证据关系 → 降级（总分 ≤30）、as_of 早于证据 → 时效性归零、空图 → 空响应、超范围分页 → 空 items。

**已知限制与盲区**：
1. **未点名客户**：NVDA 10-K 仅披露客户集中度（22%/14%）不点名 → 真实大客户（Dell 等）可能被遗漏或低估（Dell 10-K 未提及 NVIDIA，EDGAR 全文检索确认，故未纳入）；
2. **非上市/非美上市实体**：SK hynix、Samsung、Hon Hai、Wistron、Huawei、OpenAI 无 SEC 年报，交叉验证缺失，置信度天然偏低（评分引擎已如实反映）；
3. **新闻偶发误判**：未使用媒体报道作为主证据源，避免新闻误判传导；代价是部分近期合作关系覆盖不全；
4. **量化信息稀缺**：SEC 文件很少披露交易金额，多数关系为定性口径（Q=0.5）；
5. **一阶边界**：不覆盖二阶供应链传导；
6. **改进方向**：接入公司官方新闻稿与权威媒体作为第三信源、按季度滚动更新快照、对云厂商客户关系补充结构化推断。

## 10. AI 与工具使用声明（Q10）

- **使用了 LLM / 编程 Agent**（CodeBuddy，GLM 模型）：用于代码骨架生成、SEC EDGAR API 调用代码编写、以及从已抓取的 SEC 原文中辅助抽取候选提及片段；
- **人工核验**：关系类型/方向/状态/量化等级由本人在 `artisearch/annotations.py` 中逐条标注，并逐条核对自动抽取的 `source_url` 与 SEC 原文（证据片段均为 EDGAR 归档文件中的真实引用，如 CoreWeave 10-K 后续事项中的 "$2 billion" 投资披露）；测试 fixture 中的虚构公司（XYZW/ACME）仅用于边界测试并明确标注；
- **责任**：由个人对本研究的全部判断与工程质量负责；
- **未在任何工具中输入**密钥、个人数据、客户机密或未授权资料（本仓库不含任何凭据类环境变量）。

---

## 目录结构

```
├── README.md                  # 本文档（覆盖 Q1-Q10）
├── pyproject.toml             # uv/pip 依赖与项目配置
├── requirements.txt           # pip 用户等价依赖清单
├── artisearch/
│   ├── config.py              # 评分权重（公开声明）、截点、EDGAR 合规参数
│   ├── models.py              # pydantic 数据模型
│   ├── db.py                  # SQLite 读写与快照构建
│   ├── score.py               # 可解释评分引擎（纯函数）
│   ├── collect.py             # SEC EDGAR 合规采集客户端
│   ├── annotations.py         # 人工标注核验层
│   ├── api.py                 # FastAPI
│   └── cli.py                 # Typer CLI
├── data/
│   ├── snapshot.sqlite        # 评审快照（离线可复现）
│   └── raw/                   # 原始抓取存档 + manifest.jsonl
├── scripts/
│   └── collect.py             # 采集入口（可重跑）
└── tests/
    ├── fixtures/snapshot.json # Golden 数据（含边界样本）
    ├── test_score.py          # 评分引擎单测
    ├── test_api.py            # API 关键路径与边界
    └── test_db.py             # 数据层与复现性
```
