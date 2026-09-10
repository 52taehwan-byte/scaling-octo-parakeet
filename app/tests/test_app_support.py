from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from field_brain.analytics import current_money_rows, site_financial_breakdown
from field_brain.backup import create_backup, list_backups, verify_backup
from field_brain.demo import ensure_demo_data
from field_brain.originals import store_text_original
from field_brain.repository import FieldBrainRepository


def money_row(
    row_id: str,
    amount: int | None,
    purpose: str,
    progression: str,
    actualness: str,
    direction: str,
    *,
    review_status: str = "approved",
    parent: str | None = None,
) -> dict[str, object]:
    return {
        "id": row_id,
        "amount_krw": amount,
        "purpose": purpose,
        "progression": progression,
        "actualness": actualness,
        "direction": direction,
        "review_status": review_status,
        "supersedes_money_item_id": parent,
        "created_at": f"2026-08-30T00:00:{len(row_id):02d}+00:00",
    }


class AnalyticsTestCase(unittest.TestCase):
    def test_pending_correction_uses_previous_confirmed_value(self) -> None:
        rows = [
            money_row("old", 1_000_000, "base_quote", "agreed", "estimated", "inflow"),
            money_row(
                "new", 1_200_000, "base_quote", "agreed", "estimated", "inflow",
                review_status="pending", parent="old",
            ),
        ]
        heads, effective = current_money_rows(rows)
        self.assertEqual([row["id"] for row in heads], ["new"])
        self.assertEqual([row["id"] for row in effective], ["old"])
        self.assertTrue(effective[0]["has_pending_correction"])

    def test_six_field_financial_breakdown(self) -> None:
        rows = [
            money_row("q", 4_800_000, "base_quote", "agreed", "estimated", "inflow"),
            money_row("c", 480_000, "commission", "confirmed", "actual", "outflow"),
            money_row("l", 800_000, "labor_cost", "agreed", "estimated", "outflow"),
            money_row("o", 1_400_000, "outsource_cost", "agreed", "estimated", "outflow"),
            money_row("w", 600_000, "waste_cost", "confirmed", "actual", "outflow"),
            money_row("e", 220_000, "equipment_cost", "agreed", "estimated", "outflow"),
            money_row("d", 1_500_000, "deposit", "confirmed", "actual", "inflow"),
        ]
        result = site_financial_breakdown(rows, cost_completeness="complete")
        self.assertEqual(result["agreed_customer_amount"], 4_800_000)
        self.assertEqual(result["estimated_cost"], 3_020_000)
        self.assertEqual(result["estimated_margin"], 1_780_000)
        self.assertEqual(result["actual_cost"], 600_000)
        self.assertEqual(result["actual_margin"], 900_000)
        self.assertEqual(result["receivable"], 3_300_000)

    def test_unknown_cost_never_becomes_zero(self) -> None:
        rows = [
            money_row("q", 2_000_000, "base_quote", "agreed", "estimated", "inflow"),
            money_row("w", None, "waste_cost", "candidate", "estimated", "outflow"),
        ]
        result = site_financial_breakdown(rows, cost_completeness="complete")
        self.assertIsNone(result["estimated_cost"])
        self.assertIsNone(result["estimated_margin"])


class LocalFilesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_original_text_is_separate_and_hash_addressed(self) -> None:
        stored = store_text_original(
            self.root / "originals",
            "가상의 통화 원문",
            kind="call transcript",
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(stored.path.is_file())
        self.assertEqual(stored.path.read_text(encoding="utf-8"), "가상의 통화 원문\n")
        self.assertIn("sha256:", stored.evidence_ref)
        self.assertNotIn("가상의 통화 원문", stored.evidence_ref)

    def test_backup_is_verified_and_listed(self) -> None:
        db_path = self.root / "data" / "field-brain.db"
        repo = FieldBrainRepository(db_path)
        repo.create_workspace("가상 백업 사업")
        info = create_backup(db_path, self.root / "backups")
        verified = verify_backup(info.path, expected_sha256=info.sha256)
        self.assertEqual(verified.sha256, info.sha256)
        self.assertEqual([row.path for row in list_backups(self.root / "backups")], [info.path])

    def test_demo_seed_is_fictional_and_idempotent(self) -> None:
        repo = FieldBrainRepository(self.root / "demo.db")
        first = ensure_demo_data(repo)
        second = ensure_demo_data(repo)
        self.assertEqual(first, second)
        self.assertEqual(len(repo.list_workspaces()), 1)
        sites = repo.list_sites(first)
        self.assertEqual(len(sites), 2)
        self.assertTrue(all("가상" in str(site["name"]) for site in sites))


if __name__ == "__main__":
    unittest.main()
