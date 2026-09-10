from __future__ import annotations

import unittest

from field_brain.ai_contract import (
    AIContractError,
    context_envelope,
    proposal_json_schema,
    validate_proposal_envelope,
)


class AIContractTestCase(unittest.TestCase):
    def test_structured_output_schema_is_strict_and_versioned(self) -> None:
        schema = proposal_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["contract_version"]["const"], "1.0")
        proposal = schema["properties"]["proposals"]["items"]
        self.assertFalse(proposal["additionalProperties"])
        self.assertEqual(proposal["properties"]["operation"]["const"], "create")

    def test_context_is_versioned_and_rejects_local_secrets(self) -> None:
        context = context_envelope(
            workspace_id="workspace-1", workspace_revision=2, site_id="site-1", site_revision=4,
            user_message="이 현장의 다음 할 일을 분석해 줘.",
            source_metadata=[{"source_id": "source-1", "source_type": "manual_input"}],
        )
        self.assertEqual(context["contract_version"], "1.0")
        self.assertEqual(context["privacy_mode"], "minimized")
        with self.assertRaises(AIContractError):
            context_envelope(
                workspace_id="workspace-1", workspace_revision=2, user_message="분석",
                source_metadata=[{"storage_uri": "local://secret"}],
            )

    def test_gpt_output_is_forced_to_pending(self) -> None:
        payload = {
            "contract_version": "1.0", "request_id": "request-1", "workspace_id": "workspace-1",
            "site_id": "site-1", "base_revision": 7,
            "proposals": [{
                "proposal_id": "proposal-1", "proposal_type": "task", "operation": "create",
                "values": {"title": "가상 범위 확인"}, "evidence_ids": ["evidence-1"],
                "epistemic_type": "recommendation", "confidence": 0.8,
            }],
            "questions": [], "assistant_message": "확인이 필요한 할 일을 제안했습니다.",
        }
        result = validate_proposal_envelope(
            payload, expected_request_id="request-1", expected_workspace_id="workspace-1",
            expected_site_id="site-1", current_base_revision=7,
        )
        self.assertEqual(result["proposals"][0]["values"]["review_status"], "pending")

    def test_gpt_cannot_confirm_or_apply_stale_or_cross_workspace_output(self) -> None:
        base = {
            "contract_version": "1.0", "request_id": "request-1", "workspace_id": "workspace-1",
            "site_id": None, "base_revision": 1, "questions": [], "assistant_message": "제안",
            "proposals": [{
                "proposal_id": "p1", "proposal_type": "insight", "operation": "create",
                "values": {"title": "가상 제안", "review_status": "approved"},
                "evidence_ids": [], "epistemic_type": "recommendation",
            }],
        }
        with self.assertRaises(AIContractError):
            validate_proposal_envelope(
                base, expected_request_id="request-1", expected_workspace_id="workspace-1",
                expected_site_id=None, current_base_revision=1,
            )
        stale = {**base, "proposals": [], "base_revision": 1}
        with self.assertRaises(AIContractError):
            validate_proposal_envelope(
                stale, expected_request_id="request-1", expected_workspace_id="workspace-1",
                expected_site_id=None, current_base_revision=2,
            )


if __name__ == "__main__":
    unittest.main()
