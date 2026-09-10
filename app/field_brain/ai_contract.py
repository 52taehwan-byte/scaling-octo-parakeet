"""Versioned Field Brain ↔ GPT envelopes.

This module does not call a model.  It defines the boundary that every future
provider adapter must pass before data can become a pending local proposal.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "1.0"
PROPOSAL_TYPES = {"event", "task", "money_item", "risk", "insight"}
EPISTEMIC_TYPES = {"claim", "inference", "decision", "recommendation"}
OUTBOUND_SECRET_FIELDS = {
    "phone_encrypted",
    "messenger_handle_encrypted",
    "storage_uri",
    "api_key",
}
INBOUND_FORBIDDEN_FIELDS = OUTBOUND_SECRET_FIELDS | {
    "approved",
    "corrected",
    "reviewed_at",
    "reviewer_person_id",
    "created_by",
}


class AIContractError(ValueError):
    pass


def proposal_json_schema() -> dict[str, Any]:
    """Schema passed to Responses API Structured Outputs."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "contract_version", "request_id", "workspace_id", "site_id", "base_revision",
            "proposals", "questions", "assistant_message",
        ],
        "properties": {
            "contract_version": {"type": "string", "const": CONTRACT_VERSION},
            "request_id": {"type": "string", "minLength": 1},
            "workspace_id": {"type": "string", "minLength": 1},
            "site_id": {"type": ["string", "null"]},
            "base_revision": {"type": "integer", "minimum": 1},
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "proposal_id", "proposal_type", "operation", "values",
                        "evidence_ids", "epistemic_type", "confidence",
                    ],
                    "properties": {
                        "proposal_id": {"type": "string", "minLength": 1},
                        "proposal_type": {"type": "string", "enum": sorted(PROPOSAL_TYPES)},
                        "operation": {"type": "string", "const": "create"},
                        "values": {"type": "object"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "epistemic_type": {"type": "string", "enum": sorted(EPISTEMIC_TYPES)},
                        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                    },
                },
            },
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question", "reason", "required_fields"],
                    "properties": {
                        "question": {"type": "string"},
                        "reason": {"type": "string"},
                        "required_fields": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "assistant_message": {"type": "string"},
        },
    }


def context_envelope(
    *,
    workspace_id: str,
    workspace_revision: int,
    user_message: str,
    site_id: str | None = None,
    site_revision: int | None = None,
    confirmed_records: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    pending_records: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    source_metadata: Sequence[Mapping[str, Any]] | None = None,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    privacy_mode: str = "minimized",
    request_id: str | None = None,
) -> dict[str, Any]:
    if not workspace_id or workspace_revision < 1:
        raise AIContractError("workspace ID and positive revision are required")
    if site_id is None and site_revision is not None:
        raise AIContractError("site_revision requires site_id")
    if site_id is not None and (site_revision is None or site_revision < 1):
        raise AIContractError("site_id requires a positive site_revision")
    if not str(user_message).strip():
        raise AIContractError("user_message is required")
    if privacy_mode not in {"minimized", "full_with_consent"}:
        raise AIContractError("invalid privacy_mode")
    envelope = {
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": workspace_id,
        "workspace_revision": workspace_revision,
        "site_id": site_id,
        "site_revision": site_revision,
        "user_message": str(user_message).strip(),
        "confirmed_records": dict(confirmed_records or {}),
        "pending_records": dict(pending_records or {}),
        "source_metadata": list(source_metadata or []),
        "evidence": list(evidence or []),
        "privacy_mode": privacy_mode,
    }
    _reject_forbidden_keys(envelope, OUTBOUND_SECRET_FIELDS)
    return envelope


def validate_proposal_envelope(
    payload: Mapping[str, Any],
    *,
    expected_request_id: str,
    expected_workspace_id: str,
    expected_site_id: str | None,
    current_base_revision: int,
) -> dict[str, Any]:
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise AIContractError("unsupported contract_version")
    if payload.get("request_id") != expected_request_id:
        raise AIContractError("request_id mismatch")
    if payload.get("workspace_id") != expected_workspace_id:
        raise AIContractError("workspace_id mismatch")
    if payload.get("site_id") != expected_site_id:
        raise AIContractError("site_id mismatch")
    if payload.get("base_revision") != current_base_revision:
        raise AIContractError("base revision changed; proposal must be regenerated")
    proposals = payload.get("proposals")
    questions = payload.get("questions")
    if not isinstance(proposals, list) or not isinstance(questions, list):
        raise AIContractError("proposals and questions must be arrays")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for proposal in proposals:
        if not isinstance(proposal, Mapping):
            raise AIContractError("each proposal must be an object")
        proposal_id = str(proposal.get("proposal_id") or "").strip()
        if not proposal_id or proposal_id in seen:
            raise AIContractError("proposal_id must be present and unique")
        seen.add(proposal_id)
        if proposal.get("proposal_type") not in PROPOSAL_TYPES:
            raise AIContractError("invalid proposal_type")
        if proposal.get("operation") != "create":
            raise AIContractError("only create proposals are allowed in contract 1.0")
        if proposal.get("epistemic_type") not in EPISTEMIC_TYPES:
            raise AIContractError("invalid epistemic_type")
        values = proposal.get("values")
        evidence_ids = proposal.get("evidence_ids")
        if not isinstance(values, Mapping) or not isinstance(evidence_ids, list):
            raise AIContractError("proposal values must be an object and evidence_ids an array")
        _reject_forbidden_keys(values, INBOUND_FORBIDDEN_FIELDS)
        if values.get("review_status") not in {None, "pending"}:
            raise AIContractError("GPT cannot confirm a proposal")
        confidence = proposal.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
            raise AIContractError("confidence must be between 0 and 1")
        normalized.append({**dict(proposal), "values": {**dict(values), "review_status": "pending"}})
    message = payload.get("assistant_message")
    if not isinstance(message, str):
        raise AIContractError("assistant_message must be text")
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": expected_request_id,
        "workspace_id": expected_workspace_id,
        "site_id": expected_site_id,
        "base_revision": current_base_revision,
        "proposals": normalized,
        "questions": questions,
        "assistant_message": message,
    }


def _reject_forbidden_keys(value: Any, forbidden_fields: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in forbidden_fields:
                raise AIContractError(f"forbidden GPT bridge field: {key}")
            _reject_forbidden_keys(nested, forbidden_fields)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_forbidden_keys(nested, forbidden_fields)
