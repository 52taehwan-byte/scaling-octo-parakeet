from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from field_brain.db import connect, migrate, schema_version, transaction
from field_brain.repository import (
    ConflictError,
    FieldBrainRepository,
    ValidationError,
)


class CoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "field-brain-test.db"
        self.repo = FieldBrainRepository(self.db_path)
        self.workspace = self.repo.create_workspace("가상 현장 사업")
        self.site = self.repo.create_site(self.workspace["id"], "가상 A 현장")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_money(
        self,
        lineage: str,
        amount: int | None,
        purpose: str,
        progression: str,
        actualness: str,
        direction: str,
        **kwargs,
    ):
        return self.repo.create_money_item(
            self.site["id"], lineage, lineage, amount, purpose, progression,
            actualness, direction, **kwargs,
        )

    def test_migrations_enable_fk_wal_and_user_version(self) -> None:
        self.assertEqual(schema_version(self.db_path), 9)
        connection = connect(self.db_path)
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            connection.close()

    def test_migration_can_stop_then_upgrade(self) -> None:
        old_path = Path(self.temp.name) / "upgrade.db"
        self.assertEqual(migrate(old_path, target_version=1), 1)
        self.assertEqual(migrate(old_path), 9)
        self.assertEqual(schema_version(old_path), 9)

    def test_transaction_rolls_back_as_one_unit(self) -> None:
        before = len(self.repo.audit_log(self.workspace["id"]))
        with self.assertRaises(RuntimeError):
            with transaction(self.db_path) as connection:
                connection.execute(
                    """INSERT INTO audit_events
                    (id,workspace_id,action_type,target_type,target_id,actor_type,occurred_at)
                    VALUES ('temporary', ?, 'test', 'site', ?, 'system', '2026-08-30T00:00:00+00:00')""",
                    (self.workspace["id"], self.site["id"]),
                )
                raise RuntimeError("stop")
        self.assertEqual(len(self.repo.audit_log(self.workspace["id"])), before)

    def test_cross_workspace_person_reference_is_rejected(self) -> None:
        other_workspace = self.repo.create_workspace("다른 가상 사업")
        outsider = self.repo.create_person(other_workspace["id"], "가상 외부인")
        with self.assertRaisesRegex(ValidationError, "same workspace"):
            self.repo.create_site_role(self.site["id"], outsider["id"], "employee")

    def test_approved_inference_requires_evidence(self) -> None:
        with self.assertRaisesRegex(ValidationError, "evidence_ref"):
            self.repo.create_task(
                self.site["id"], "추론 할 일", epistemic_type="inference", review_status="approved"
            )
        task = self.repo.create_task(
            self.site["id"], "검토할 추론", epistemic_type="inference", review_status="pending"
        )
        with self.assertRaisesRegex(ValidationError, "evidence_ref"):
            self.repo.review_target("task", task["id"], "approve")

    def test_margin_is_unknown_until_cost_scope_is_complete(self) -> None:
        self.add_money("quote", 5_000_000, "base_quote", "agreed", "estimated", "inflow")
        self.add_money("labor", 1_000_000, "labor_cost", "candidate", "estimated", "outflow")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertEqual(summary.margin_status, "unknown_costs")
        self.assertIsNone(summary.margin_krw)

        self.repo.set_site_cost_completeness(self.site["id"], "complete", reason="가상 비용 전체 확인")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertEqual(summary.margin_krw, 4_000_000)
        self.assertEqual(summary.cost_basis, "estimated")

    def test_pending_cost_makes_margin_unknown_even_if_marked_complete(self) -> None:
        self.add_money("quote", 2_000_000, "base_quote", "agreed", "estimated", "inflow")
        cost = self.add_money(
            "waste", 400_000, "waste_cost", "candidate", "estimated", "outflow",
            review_status="pending",
        )
        self.repo.set_site_cost_completeness(self.site["id"], "complete", reason="목록 작성 완료")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertEqual(summary.margin_status, "unknown_costs")
        self.assertEqual(summary.unresolved_cost_items, 1)

        self.repo.review_target("money_item", cost["id"], "approve")
        self.assertEqual(self.repo.site_financial_summary(self.site["id"]).margin_krw, 1_600_000)

    def test_pending_correction_keeps_prior_approved_value_but_blocks_cost_margin(self) -> None:
        quote = self.add_money("quote", 2_000_000, "base_quote", "agreed", "estimated", "inflow")
        self.add_money(
            "quote", 2_200_000, "base_quote", "agreed", "estimated", "inflow",
            review_status="pending", supersedes_money_item_id=quote["id"],
            correction_reason="가상 변경 검토 중",
        )
        self.repo.set_site_cost_completeness(self.site["id"], "complete", reason="가상 비용 없음")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertEqual(summary.agreed_revenue_krw, 2_000_000)
        self.assertEqual(summary.margin_krw, 2_000_000)

    def test_offered_and_agreed_quote_are_not_double_counted(self) -> None:
        offered = self.add_money(
            "quote", 5_000_000, "base_quote", "offered", "estimated", "inflow"
        )
        self.add_money(
            "quote", 4_800_000, "base_quote", "agreed", "estimated", "inflow",
            supersedes_money_item_id=offered["id"], correction_reason="가상 합의액 반영",
            review_status="corrected",
        )
        self.repo.set_site_cost_completeness(self.site["id"], "complete", reason="가상 무비용 현장")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertEqual(summary.agreed_revenue_krw, 4_800_000)
        self.assertEqual(summary.margin_krw, 4_800_000)

    def test_money_lineage_cannot_fork_or_duplicate_root(self) -> None:
        root = self.add_money("quote", 1_000_000, "base_quote", "offered", "estimated", "inflow")
        replacement = self.add_money(
            "quote", 900_000, "base_quote", "agreed", "estimated", "inflow",
            supersedes_money_item_id=root["id"], correction_reason="가상 조정",
        )
        self.assertEqual(replacement["supersedes_money_item_id"], root["id"])
        with self.assertRaises(ConflictError):
            self.add_money(
                "quote", 800_000, "base_quote", "agreed", "estimated", "inflow",
                supersedes_money_item_id=root["id"], correction_reason="잘못된 분기",
            )
        with self.assertRaises(ConflictError):
            self.add_money("quote", 700_000, "base_quote", "offered", "estimated", "inflow")

    def test_money_amount_is_integer_won_and_direction_is_validated(self) -> None:
        with self.assertRaisesRegex(ValidationError, "integer"):
            self.add_money("bad", 10.5, "labor_cost", "candidate", "estimated", "outflow")
        with self.assertRaisesRegex(ValidationError, "inflow"):
            self.add_money("bad", 10, "base_quote", "agreed", "estimated", "outflow")

    def test_unknown_money_amount_is_null_not_zero_and_blocks_margin(self) -> None:
        self.add_money("quote", 2_000_000, "base_quote", "agreed", "estimated", "inflow")
        unknown = self.add_money(
            "unknown-waste", None, "waste_cost", "candidate", "estimated", "outflow"
        )
        self.assertIsNone(unknown["amount_krw"])
        self.repo.set_site_cost_completeness(self.site["id"], "complete", reason="항목 목록 확인")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertIsNone(summary.cost_krw)
        self.assertIsNone(summary.margin_krw)
        self.assertEqual(summary.unresolved_cost_items, 1)

    def test_only_current_correction_is_reviewable_and_counted(self) -> None:
        root = self.add_money(
            "quote", 1_000_000, "base_quote", "offered", "estimated", "inflow",
            review_status="pending",
        )
        current = self.add_money(
            "quote", 900_000, "base_quote", "agreed", "estimated", "inflow",
            review_status="pending", supersedes_money_item_id=root["id"],
            correction_reason="가상 정정 제안",
        )
        pending = self.repo.pending_reviews(self.workspace["id"])
        self.assertEqual([row["id"] for row in pending], [current["id"]])
        self.assertEqual(self.repo.dashboard(self.workspace["id"])["pending_review"], 1)
        with self.assertRaises(ConflictError):
            self.repo.review_target("money_item", root["id"], "approve")

    def test_ai_cannot_confirm_or_review_records(self) -> None:
        with self.assertRaisesRegex(ValidationError, "AI-created"):
            self.repo.create_task(
                self.site["id"], "AI가 확정하면 안 되는 일", review_status="approved", actor_type="ai"
            )
        task = self.repo.create_task(
            self.site["id"], "AI 검토 후보", review_status="pending", actor_type="ai"
        )
        with self.assertRaisesRegex(ValidationError, "only a user"):
            self.repo.review_target("task", task["id"], "approve", actor_type="ai")

    def test_discount_without_base_quote_does_not_create_known_margin(self) -> None:
        self.add_money("discount", 100_000, "discount", "agreed", "estimated", "neutral")
        self.repo.set_site_cost_completeness(self.site["id"], "complete", reason="비용 없음 확인")
        summary = self.repo.site_financial_summary(self.site["id"])
        self.assertIsNone(summary.agreed_revenue_krw)
        self.assertEqual(summary.margin_status, "unknown_revenue")

    def test_risk_cannot_close_without_resolution_evidence(self) -> None:
        with self.assertRaisesRegex(ValidationError, "resolution"):
            self.repo.create_risk(
                self.site["id"], "safety", "가상 안전 위험", business_status="closed"
            )

    def test_review_and_audit_are_structured_and_immutable(self) -> None:
        task = self.repo.create_task(
            self.site["id"], "가상 검토 항목", review_status="pending"
        )
        review = self.repo.review_target("task", task["id"], "hold", reason="근거 대기")
        self.assertEqual(review["action"], "hold")
        self.assertEqual(self.repo.pending_reviews(self.workspace["id"])[0]["review_status"], "held")

        audits = self.repo.audit_log(self.workspace["id"])
        self.assertTrue(any(row["target_type"] == "task" and row["action_type"] == "review" for row in audits))
        connection = connect(self.db_path)
        try:
            audit_id = audits[0]["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM audit_events WHERE id = ?", (audit_id,))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
