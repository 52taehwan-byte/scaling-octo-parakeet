from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from field_brain.migration_preview import preview_codex_static_mvp, write_preview


FIXTURE = '''
const seedData = { sites: [{
 id: "fictional-site", name: "가상 철거 현장", address: "가상시 예시구", status: "quoted", directDays: 2,
 quote: 1000000, fee: 0, executionCost: null,
 scope: ["가상 가벽 철거"],
 records: [
  rec("2026-01-02","quote","가상 고객 견적 100만원.","confirmed",1000000,"customer_quote"),
  rec("2026-01-02","risk","범위 확인 필요.","needs_review")
 ]
}]};
'''


class MigrationPreviewTestCase(unittest.TestCase):
    def test_preview_never_inherits_confirmation_or_executes_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "app.js"
            source.write_text(FIXTURE, encoding="utf-8")
            report = preview_codex_static_mvp(source)
            self.assertEqual(report["counts"], {"sites": 1, "candidates": 5})
            self.assertFalse(report["safety"]["source_executed"])
            self.assertFalse(report["safety"]["database_written"])
            self.assertTrue(report["safety"]["all_candidates_require_review"])
            site = report["sites"][0]
            self.assertEqual(site["review_status"], "pending")
            self.assertTrue(all(row["review_status"] == "pending" for row in site["record_candidates"]))
            self.assertEqual(site["duplicate_amounts_krw"], [1_000_000])
            self.assertIsNone(site["summary_candidates"][1]["amount_krw"])
            self.assertEqual(site["summary_candidates"][1]["legacy_amount_krw"], 0)
            self.assertIsNone(site["summary_candidates"][2]["amount_krw"])

    def test_written_preview_is_outside_source_and_preserves_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source" / "app.js"
            source.parent.mkdir()
            source.write_text(FIXTURE, encoding="utf-8")
            output = Path(tmp) / "local-data" / "preview.json"
            write_preview(source, output)
            self.assertTrue(output.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), FIXTURE)


if __name__ == "__main__":
    unittest.main()
