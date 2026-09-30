import copy
import unittest
from field_brain.ledger_transfer_plan import plan_cost_transfer


def cost(**changes):
    return dict({"id":"a", "workspace_id":"w", "site_id":"s", "lineage_key":"fuel",
        "review_status":"approved", "purpose":"transport_cost", "progression":"confirmed",
        "actualness":"actual", "direction":"outflow", "amount_krw":30000,
        "evidence_ref":"fictional-source", "occurred_at":"2026-09-30T12:00+09:00"}, **changes)


class LedgerTransferPlanTests(unittest.TestCase):
    def test_candidate_is_not_permission_and_original_unchanged(self):
        rows=[cost()]; original=copy.deepcopy(rows)
        item=plan_cost_transfer(rows)["items"][0]
        self.assertEqual(item["reason"], "candidate_requires_link_and_duplicate_checks")
        self.assertFalse(item["automatic_transfer_allowed"])
        self.assertEqual(rows, original)

    def test_pending_correction_does_not_export_old_cost_as_final(self):
        plan=plan_cost_transfer([cost(),cost(id="b",review_status="pending",supersedes_money_item_id="a")])
        self.assertEqual(plan["items"][0]["reason"], "unresolved_correction")
        self.assertEqual(plan["items"][0]["revision_ids"], ["b","a"])

    def test_approved_correction_is_one_item_with_history(self):
        plan=plan_cost_transfer([cost(),cost(id="b",amount_krw=40000,supersedes_money_item_id="a")])
        self.assertEqual(len(plan["items"]), 1)
        self.assertEqual(plan["items"][0]["effective_id"], "b")
        self.assertEqual(plan["raw_revision_count"], 2)

    def test_estimates_quotes_commission_and_scrap_not_costs(self):
        for changes in ({"actualness":"estimated"},{"purpose":"base_quote"},
                {"purpose":"commission"},{"purpose":"scrap_income","direction":"inflow"}):
            self.assertNotEqual(plan_cost_transfer([cost(**changes)])["items"][0]["reason"],
                "candidate_requires_link_and_duplicate_checks")

    def test_unknown_date_amount_and_missing_evidence_not_invented(self):
        for changes in ({"amount_krw":None},{"amount_krw":True},{"occurred_at":None},
                {"occurred_at":"2026-09-30"},{"evidence_ref":None}):
            self.assertNotEqual(plan_cost_transfer([cost(**changes)])["items"][0]["reason"],
                "candidate_requires_link_and_duplicate_checks")

    def test_invalid_chains_fail_closed(self):
        cases=[[cost(),cost()], [cost(supersedes_money_item_id="missing")],
            [cost(supersedes_money_item_id="a")],
            [cost(),cost(id="b",site_id="another",supersedes_money_item_id="a")],
            [cost(),cost(id="b",supersedes_money_item_id="a"),cost(id="c",supersedes_money_item_id="a")]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                plan_cost_transfer(rows)
