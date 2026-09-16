"""Data layer tests: snapshot building, filtering, graph construction."""

from __future__ import annotations

from datetime import date

from artisearch import db, score
from artisearch.models import RelationshipType


class TestSnapshotBuild:
    def test_build_from_fixture(self, snapshot_db_path):
        conn = db.connect(snapshot_db_path)
        try:
            companies = db.list_companies(conn)
            rels, total = db.list_relationships(conn, page_size=100)
            assert len(companies) == 9
            assert total == 8
            # 每条关系都已评分并带分项明细
            for r in rels:
                assert 0 <= r.confidence_score <= 100
                assert r.score_breakdown.credibility is not None
        finally:
            conn.close()

    def test_idempotent_rebuild(self, snapshot_db_path, tmp_path):
        """同 fixture 重建 → 数据一致（可复现）。"""
        from tests.conftest import SNAPSHOT_JSON

        rebuilt = tmp_path / "rebuilt.sqlite"
        db.build_snapshot_from_json(SNAPSHOT_JSON, rebuilt, scored=False)
        c1, c2 = db.connect(snapshot_db_path), db.connect(rebuilt)
        try:
            r1, t1 = db.list_relationships(c1, page_size=100)
            r2, t2 = db.list_relationships(c2, page_size=100)
            assert t1 == t2
            assert [x.model_dump() for x in r1] == [x.model_dump() for x in r2]
        finally:
            c1.close()
            c2.close()


class TestFilters:
    def test_company_filter_matches_subject_or_object(self, conn):
        items, total = db.list_relationships(conn, company="tsm")
        assert total == 1
        assert items[0].object == "TSM"

    def test_type_filter(self, conn):
        _, total = db.list_relationships(conn, relationship_type=RelationshipType.peer)
        assert total == 4

    def test_min_relevance_filter(self, conn):
        _, total = db.list_relationships(conn, min_relevance=100)
        assert total == 0

    def test_as_of_filter(self, conn):
        _, total = db.list_relationships(conn, as_of=date(2026, 1, 1))
        assert total == 1

    def test_combined_filters(self, conn):
        _, total = db.list_relationships(
            conn, relationship_type=RelationshipType.peer, min_relevance=50
        )
        assert total >= 1


class TestGraph:
    def test_graph_from_conn(self, conn):
        g = db.get_graph(conn)
        assert len(g.edges) == 8
        assert g.as_of == date(2026, 9, 16)
        labels = {n.id: n.label for n in g.nodes}
        assert labels["NVDA"] == "NVIDIA Corporation"

    def test_graph_empty(self, empty_db_path):
        conn = db.connect(empty_db_path)
        try:
            g = db.get_graph(conn)
            assert g.nodes == []
            assert g.edges == []
        finally:
            conn.close()


class TestScoringIntegration:
    def test_scores_are_deterministic_across_rebuild(self, snapshot_db_path):
        from tests.conftest import SNAPSHOT_JSON

        rebuilt = snapshot_db_path.parent / "rebuilt2.sqlite"
        db.build_snapshot_from_json(SNAPSHOT_JSON, rebuilt, scored=False)
        c1, c2 = db.connect(snapshot_db_path), db.connect(rebuilt)
        try:
            r1, _ = db.list_relationships(c1, page_size=100)
            r2, _ = db.list_relationships(c2, page_size=100)
            scores1 = {r.id: r.confidence_score for r in r1}
            scores2 = {r.id: r.confidence_score for r in r2}
            assert scores1 == scores2
        finally:
            c1.close()
            c2.close()
