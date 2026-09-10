from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from field_brain.db import connect, schema_version
from field_brain.repository import ConflictError, FieldBrainRepository, ValidationError


class KnowledgeFoundationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "field-brain.db"
        self.repo = FieldBrainRepository(self.db_path)
        self.workspace = self.repo.create_workspace("가상 작업공간")
        self.site = self.repo.create_site(self.workspace["id"], "가상 현장")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_source_evidence_and_pending_ai_insight_are_linked(self) -> None:
        source = self.repo.create_source(
            self.workspace["id"], "manual_input", "a" * 64, "local://originals/fictional.txt",
            site_id=self.site["id"], original_name="가상 메모.txt", mime_type="text/plain", byte_size=30,
        )
        evidence = self.repo.create_evidence(
            source["id"], "text_range", text_start=0, text_end=10, excerpt="가상 근거",
        )
        insight = self.repo.create_insight(
            self.workspace["id"], "next_action", "범위 확인", "작업 전에 범위를 확인하세요.",
            site_id=self.site["id"], evidence_id=evidence["id"], confidence=0.7,
        )
        self.assertEqual(insight["review_status"], "pending")
        self.assertEqual(self.repo.list_sources(self.workspace["id"])[0]["id"], source["id"])
        self.assertEqual(self.repo.list_insights(self.workspace["id"], review_status="pending")[0]["id"], insight["id"])
        with closing(connect(self.db_path)) as connection:
            audit = connection.execute(
                "SELECT after_json FROM audit_events WHERE target_type = 'source' AND target_id = ?",
                (source["id"],),
            ).fetchone()[0]
            self.assertNotIn("local://originals", audit)
            evidence_audit = connection.execute(
                "SELECT after_json FROM audit_events WHERE target_type = 'evidence' AND target_id = ?",
                (evidence["id"],),
            ).fetchone()[0]
            self.assertNotIn("가상 근거", evidence_audit)

    def test_source_hash_is_deduplicated_and_ai_cannot_approve_insight(self) -> None:
        self.repo.create_source(self.workspace["id"], "document", "b" * 64, "local://one")
        with self.assertRaises(ConflictError):
            self.repo.create_source(self.workspace["id"], "document", "b" * 64, "local://two")
        with self.assertRaises(ValidationError):
            self.repo.create_insight(
                self.workspace["id"], "learning", "가상 분석", "가상 본문", review_status="approved"
            )

    def test_evidence_ranges_and_workspace_boundaries_are_validated(self) -> None:
        source = self.repo.create_source(self.workspace["id"], "audio", "c" * 64, "local://audio")
        with self.assertRaises(ValidationError):
            self.repo.create_evidence(source["id"], "audio_range", audio_start_ms=2000, audio_end_ms=1000)
        other_workspace = self.repo.create_workspace("다른 가상 작업공간")
        with self.assertRaises(ValidationError):
            self.repo.create_insight(
                other_workspace["id"], "safety", "가상 위험", "확인이 필요합니다.",
                evidence_id=self.repo.create_evidence(source["id"], "whole_source")["id"],
            )

    def test_schema_version_is_nine(self) -> None:
        self.assertEqual(schema_version(self.db_path), 9)


if __name__ == "__main__":
    unittest.main()
