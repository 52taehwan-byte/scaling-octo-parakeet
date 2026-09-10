import unittest

from field_brain.workflow import build_workflow_focus


class WorkflowFocusTests(unittest.TestCase):
    def focus(self, status: str, **overrides):
        site = {
            "business_status": status,
            "scope_summary": "화장실 철거",
            "planned_start_at": "2026-09-02",
            "cost_completeness": "complete",
        }
        site.update(overrides.pop("site", {}))
        return build_workflow_focus(
            site,
            tasks=overrides.pop("tasks", []),
            risks=overrides.pop("risks", []),
            financial=overrides.pop("financial", {}),
            pending_review_count=overrides.pop("pending_review_count", 0),
        )

    def test_lead_without_scope_asks_for_scope_first(self):
        result = self.focus("lead", site={"scope_summary": None})
        self.assertEqual(result["phase_label"], "문의")
        self.assertEqual(result["primary_action"]["kind"], "scope")

    def test_high_risk_outranks_normal_work_task(self):
        result = self.focus(
            "in_progress",
            tasks=[{"title": "폐기물 반출", "status": "open", "is_overdue": False}],
            risks=[{"title": "고객 추가 작업 미합의", "severity": "high", "is_resolved": False}],
        )
        self.assertEqual(result["primary_action"]["kind"], "risk")
        self.assertTrue(result["has_urgent_issue"])

    def test_overdue_task_outranks_pending_review(self):
        result = self.focus(
            "scheduled",
            tasks=[{"title": "장비 예약", "status": "open", "is_overdue": True}],
            pending_review_count=2,
        )
        self.assertEqual(result["primary_action"]["kind"], "overdue_task")

    def test_completed_site_with_incomplete_cost_checks_actual_cost(self):
        result = self.focus("completed", site={"cost_completeness": "partial"})
        self.assertEqual(result["primary_action"]["kind"], "cost")

    def test_completed_site_does_not_create_receivable_work(self):
        result = self.focus("completed", financial={"receivable": 1_800_000})
        actions = [result["primary_action"], *result["secondary_actions"]]
        self.assertNotIn("receivable", {action["kind"] for action in actions})
        self.assertEqual(result["primary_action"]["kind"], "completion")

    def test_settled_site_turns_experience_into_knowledge(self):
        result = self.focus("settled")
        self.assertEqual(result["phase_label"], "회고")
        self.assertEqual(result["primary_action"]["kind"], "retrospective")


if __name__ == "__main__":
    unittest.main()
