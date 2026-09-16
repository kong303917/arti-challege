"""API tests (Q9 关键路径 + 边界)：列表/单条/图/公司画像 + 404/422/分页/边界样本。"""

from __future__ import annotations


class TestCompanies:
    def test_company_profile(self, client):
        resp = client.get("/api/companies/NVDA")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticker"] == "NVDA"
        assert body["exchange"] == "Nasdaq"
        assert body["security_id"] == "SEC-CIK-0001045810"
        assert body["is_public"] is True

    def test_company_case_insensitive(self, client):
        assert client.get("/api/companies/nvda").status_code == 200

    def test_company_private_no_exchange(self, client):
        body = client.get("/api/companies/OPENAI").json()
        assert body["is_public"] is False
        assert body["exchange"] is None

    def test_unknown_ticker_404(self, client):
        resp = client.get("/api/companies/XXXX")
        assert resp.status_code == 404
        assert "未知公司" in resp.json()["detail"]


class TestRelationshipsList:
    def test_list_default(self, client):
        resp = client.get("/api/relationships")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 8
        assert body["page"] == 1
        assert len(body["items"]) == 8
        # 按 confidence_score 降序
        scores = [i["confidence_score"] for i in body["items"]]
        assert scores == sorted(scores, reverse=True)

    def test_filter_by_type(self, client):
        body = client.get("/api/relationships?type=supplier").json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "NVDA-TSM-supplier"

    def test_filter_by_company(self, client):
        body = client.get("/api/relationships?company=TSM").json()
        assert body["total"] == 1
        body = client.get("/api/relationships?company=NVDA").json()
        assert body["total"] == 8

    def test_filter_by_min_relevance(self, client):
        body = client.get("/api/relationships?min_relevance=80").json()
        assert all(i["confidence_score"] >= 80 for i in body["items"])
        assert body["total"] >= 1

    def test_filter_by_as_of(self, client):
        # as_of=2026-01-01 只保留 as_of 不晚于该日期的记录（AMZN 样本）
        body = client.get("/api/relationships?as_of=2026-01-01").json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "NVDA-AMZN-peer"
        # as_of=2025-01-01 → 空
        body = client.get("/api/relationships?as_of=2025-01-01").json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_pagination(self, client):
        body = client.get("/api/relationships?page=1&page_size=3").json()
        assert len(body["items"]) == 3
        assert body["total"] == 8
        page2 = client.get("/api/relationships?page=2&page_size=3").json()
        assert [i["id"] for i in page2["items"]] != [i["id"] for i in body["items"]]
        # 超出范围的页 → 空 items，total 不变
        body = client.get("/api/relationships?page=99&page_size=3").json()
        assert body["items"] == []
        assert body["total"] == 8

    # ---------------- 边界：422 ----------------
    def test_invalid_type_422(self, client):
        resp = client.get("/api/relationships?type=badtype")
        assert resp.status_code == 422
        assert resp.json()["detail"]

    def test_invalid_page_422(self, client):
        assert client.get("/api/relationships?page=0").status_code == 422
        assert client.get("/api/relationships?page=-1").status_code == 422

    def test_invalid_page_size_422(self, client):
        assert client.get("/api/relationships?page_size=0").status_code == 422
        assert client.get("/api/relationships?page_size=101").status_code == 422

    def test_invalid_min_relevance_422(self, client):
        assert client.get("/api/relationships?min_relevance=101").status_code == 422
        assert client.get("/api/relationships?min_relevance=-1").status_code == 422

    def test_invalid_as_of_format_422(self, client):
        assert client.get("/api/relationships?as_of=2026/09/16").status_code == 422
        assert client.get("/api/relationships?as_of=not-a-date").status_code == 422


class TestRelationshipDetail:
    def test_detail_with_evidence(self, client):
        resp = client.get("/api/relationships/NVDA-TSM-supplier")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "TSM"
        assert body["status"] == "fact"
        assert len(body["evidence"]) == 1
        assert body["evidence"][0]["source_url"].startswith("https://www.sec.gov")

    def test_detail_score_breakdown(self, client):
        body = client.get("/api/relationships/NVDA-AMD-peer").json()
        sb = body["score_breakdown"]
        for factor in ("credibility", "independence", "timeliness", "relation_strength", "quantifiability"):
            detail = sb[factor]
            assert 0.0 <= detail["value"] <= 1.0
            assert detail["explanation"]
        assert 0 <= body["confidence_score"] <= 100

    def test_unknown_id_404(self, client):
        resp = client.get("/api/relationships/NOPE")
        assert resp.status_code == 404
        assert "未知关系" in resp.json()["detail"]

    # ---------------- 边界样本 ----------------
    def test_empty_evidence_degraded(self, client):
        """空证据关系 → 降级（总分 ≤ 30，C/I/T 因子为 0）。"""
        body = client.get("/api/relationships/NVDA-ACME-partner").json()
        assert body["confidence_score"] <= 30
        assert body["score_breakdown"]["credibility"]["value"] == 0.0
        assert body["score_breakdown"]["independence"]["value"] == 0.0
        assert body["score_breakdown"]["timeliness"]["value"] == 0.0

    def test_future_evidence_timeliness_zero(self, client):
        """as_of 早于证据发布日期 → 时效性归零。"""
        body = client.get("/api/relationships/NVDA-MSFT-peer").json()
        assert body["score_breakdown"]["timeliness"]["value"] == 0.0

    def test_old_low_credibility_evidence_low_score(self, client):
        """低权威 + 陈旧 + 推断 → 低分，且低于 SEC 事实样本。"""
        weak = client.get("/api/relationships/NVDA-XYZW-peer").json()
        strong = client.get("/api/relationships/NVDA-TSM-supplier").json()
        assert weak["confidence_score"] < strong["confidence_score"]
        assert weak["score_breakdown"]["timeliness"]["value"] < 1.0


class TestEvidence:
    def test_evidence_list(self, client):
        resp = client.get("/api/relationships/NVDA-AMD-peer/evidence")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        publishers = {i["publisher"] for i in items}
        assert len(publishers) == 2

    def test_evidence_unknown_id_404(self, client):
        assert client.get("/api/relationships/NOPE/evidence").status_code == 404

    def test_empty_evidence_returns_empty_list(self, client):
        resp = client.get("/api/relationships/NVDA-ACME-partner/evidence")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGraph:
    def test_graph_nodes_edges(self, client):
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        body = resp.json()
        node_ids = {n["id"] for n in body["nodes"]}
        assert "NVDA" in node_ids
        assert "TSM" in node_ids
        # 8 条关系 → 8 条边
        assert len(body["edges"]) == 8
        # NVDA 被标记为研究主体
        nvda_node = next(n for n in body["nodes"] if n["id"] == "NVDA")
        assert nvda_node["is_subject"] is True
        # 边字段完整
        edge = body["edges"][0]
        assert {"id", "source", "target", "relationship_type", "direction", "confidence_score"} <= set(edge)

    def test_graph_empty(self, empty_client):
        """空图 → 明确空响应（而非 404/500）。"""
        resp = empty_client.get("/api/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes"] == []
        assert body["edges"] == []

    def test_health(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
