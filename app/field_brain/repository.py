"""Transactional repository for the Field Brain core ledger."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .db import connect, initialize, transaction
from .models import (
    AGREED_REVENUE_PROGRESSIONS,
    COST_PURPOSES,
    EFFECTIVE_REVIEW_STATUSES,
    REVENUE_PURPOSES,
    ActorType,
    Actualness,
    CostCompleteness,
    EpistemicType,
    EventStatus,
    FinancialSummary,
    LegacyStatus,
    MoneyDirection,
    MoneyProgression,
    MoneyPurpose,
    ReviewAction,
    ReviewStatus,
    RiskStatus,
    SiteStatus,
    TaskStatus,
    enum_values,
)


class FieldBrainError(RuntimeError):
    """Base class for safe, user-presentable core errors."""


class ValidationError(FieldBrainError, ValueError):
    pass


class NotFoundError(FieldBrainError, LookupError):
    pass


class ConflictError(FieldBrainError):
    pass


SITE_ROLE_TYPES = (
    "client",
    "buyer",
    "site_manager",
    "owner",
    "employee",
    "skilled_worker",
    "helper",
    "subcontractor",
    "equipment_operator",
    "waste_vendor",
    "other",
)
PERSON_KINDS = ("individual", "organization_contact", "unknown")
PRIVACY_LEVELS = ("private", "sensitive", "restricted")
TIME_PRECISIONS = ("exact", "date", "approximate", "range", "unknown")
TASK_PHASES = (
    "pre_estimate",
    "contract",
    "preparation",
    "demolition",
    "waste",
    "restoration",
    "settlement",
    "aftercare",
    "other",
)
PRIORITIES = ("low", "normal", "high", "urgent")
RISK_CATEGORIES = ("safety", "scope", "customer", "schedule", "cost", "legal", "quality", "other")
SEVERITIES = ("low", "medium", "high", "critical")
LIKELIHOODS = ("unlikely", "possible", "likely")
SOURCE_TYPES = ("audio", "transcript", "message_export", "photo", "document", "manual_input", "migration_preview", "other")
PROCESSING_STATUSES = ("unprocessed", "processing", "processed", "failed", "excluded")
EVIDENCE_TYPES = ("whole_source", "text_range", "audio_range", "image_region", "document_page", "other")
INSIGHT_TYPES = ("profitability", "schedule", "customer", "safety", "process", "learning", "question", "next_action", "other")
REVIEWABLE_TABLES = {
    "event": "events",
    "task": "tasks",
    "money_item": "money_items",
    "risk": "risks",
    "schedule_item": "schedule_items",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    return str(uuid.uuid4())


def _text(value: Any, field: str, *, required: bool = False, max_length: int = 10_000) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    cleaned = value.strip()
    if required and not cleaned:
        raise ValidationError(f"{field} is required")
    if len(cleaned) > max_length:
        raise ValidationError(f"{field} is too long")
    return cleaned or None


def _choice(value: Any, field: str, choices: Iterable[str]) -> str:
    raw = value.value if hasattr(value, "value") else value
    if raw not in choices:
        raise ValidationError(f"invalid {field}: {raw!r}")
    return str(raw)


def _iso(value: Any, field: str) -> str | None:
    raw = _text(value, field)
    if raw is None:
        return None
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(f"{field} must be an ISO 8601 date/time") from exc
    return raw


def _integer(value: Any, field: str, *, minimum: int = 0, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if value < minimum:
        raise ValidationError(f"{field} must be at least {minimum}")
    return value


def _number(value: Any, field: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a number")
    numeric = float(value)
    if numeric <= 0:
        raise ValidationError(f"{field} must be greater than zero")
    return numeric


def _json_object(value: Any, field: str) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field} must be an object")
    try:
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be JSON serializable") from exc


def _snapshot(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    # Audit metadata should not become a second store for encrypted contact data.
    for key in ("phone_encrypted", "messenger_handle_encrypted", "customer_contact", "address_text", "storage_uri", "original_name", "excerpt"):
        if key in data and data[key] is not None:
            data[key] = "[redacted]"
    if "person_kind" in data and data.get("notes") is not None:
        data["notes"] = "[redacted]"
    return data


def _json_snapshot(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FieldBrainRepository:
    """The public persistence API used by the local web layer."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        initialize(self.db_path)

    @staticmethod
    def _created_by(actor_type: str, actor_id: str | None) -> str:
        return f"{actor_type}:{actor_id}" if actor_id else actor_type

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
        actor_type: str,
        actor_id: str | None,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        reason: str | None = None,
        source_request_id: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events
            (id, workspace_id, action_type, target_type, target_id, actor_type,
             actor_id, before_json, after_json, reason, occurred_at, source_request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _new_id(),
                workspace_id,
                action_type,
                target_type,
                target_id,
                actor_type,
                actor_id,
                _json_snapshot(before),
                _json_snapshot(after),
                reason,
                _utc_now(),
                source_request_id,
            ),
        )

    @staticmethod
    def _require_row(
        connection: sqlite3.Connection,
        table: str,
        target_id: str,
        *,
        workspace_id: str | None = None,
    ) -> sqlite3.Row:
        query = f"SELECT * FROM {table} WHERE id = ? AND deleted_at IS NULL"
        params: list[Any] = [target_id]
        if workspace_id is not None:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        row = connection.execute(query, params).fetchone()
        if row is None:
            raise NotFoundError(f"{table.rstrip('s')} not found: {target_id}")
        return row

    @staticmethod
    def _require_person(
        connection: sqlite3.Connection, person_id: str | None, workspace_id: str, field: str
    ) -> None:
        if person_id is None:
            return
        row = connection.execute(
            "SELECT 1 FROM persons WHERE id = ? AND workspace_id = ? AND deleted_at IS NULL",
            (person_id, workspace_id),
        ).fetchone()
        if row is None:
            raise ValidationError(f"{field} must reference a person in the same workspace")

    @staticmethod
    def _knowledge_fields(
        epistemic_type: Any,
        review_status: Any,
        evidence_ref: Any,
    ) -> tuple[str, str, str | None]:
        epistemic = _choice(epistemic_type, "epistemic_type", enum_values(EpistemicType))
        review = _choice(review_status, "review_status", enum_values(ReviewStatus))
        evidence = _text(evidence_ref, "evidence_ref", max_length=2_000)
        if (
            epistemic in {EpistemicType.INFERENCE.value, EpistemicType.RECOMMENDATION.value}
            and review in EFFECTIVE_REVIEW_STATUSES
            and not evidence
        ):
            raise ValidationError("approved inference/recommendation requires evidence_ref")
        return epistemic, review, evidence

    @staticmethod
    def _validate_actor_review(
        actor_type: str,
        review_status: str,
        *,
        is_replacement: bool = False,
    ) -> None:
        if actor_type == ActorType.AI.value and review_status != ReviewStatus.PENDING.value:
            raise ValidationError("AI-created records must remain pending until a user reviews them")
        if review_status == ReviewStatus.CORRECTED.value and not is_replacement:
            raise ValidationError("corrected status requires a replacement record")

    def create_workspace(
        self,
        name: str,
        *,
        industry_pack_id: str = "demolition",
        industry_pack_version: str = "0.1.0",
        timezone_name: str = "Asia/Seoul",
        settings: Mapping[str, Any] | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
        legacy_status: str | LegacyStatus = LegacyStatus.NATIVE,
        legacy_ref: str | None = None,
    ) -> dict[str, Any]:
        clean_name = _text(name, "name", required=True, max_length=200)
        pack = _text(industry_pack_id, "industry_pack_id", required=True, max_length=100)
        pack_version = _text(industry_pack_version, "industry_pack_version", required=True, max_length=50)
        tz = _text(timezone_name, "timezone", required=True, max_length=100)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        legacy = _choice(legacy_status, "legacy_status", enum_values(LegacyStatus))
        target_id, now = _new_id(), _utc_now()
        values = {
            "id": target_id,
            "name": clean_name,
            "industry_pack_id": pack,
            "industry_pack_version": pack_version,
            "timezone": tz,
            "settings_json": _json_object(settings, "settings"),
            "created_at": now,
            "updated_at": now,
            "created_by": self._created_by(actor, actor_id),
            "legacy_status": legacy,
            "legacy_ref": _text(legacy_ref, "legacy_ref", max_length=2_000),
        }
        with transaction(self.db_path) as connection:
            connection.execute(
                """INSERT INTO workspaces
                (id,name,industry_pack_id,industry_pack_version,timezone,settings_json,
                 created_at,updated_at,created_by,legacy_status,legacy_ref)
                VALUES (:id,:name,:industry_pack_id,:industry_pack_version,:timezone,:settings_json,
                        :created_at,:updated_at,:created_by,:legacy_status,:legacy_ref)""",
                values,
            )
            row = self._require_row(connection, "workspaces", target_id)
            self._audit(
                connection,
                workspace_id=target_id,
                action_type="create",
                target_type="workspace",
                target_id=target_id,
                actor_type=actor,
                actor_id=actor_id,
                before=None,
                after=_snapshot(row),
            )
            return dict(row)

    def save_worker_rate(self, workspace_id: str, name: str, amount: int, effective_on: str) -> None:
        name = _text(name, 'worker_name', required=True, max_length=100)
        amount = _integer(amount, 'daily_rate')
        if amount <= 0 or amount > 10000000:
            raise ValidationError('일당은 1원에서 1,000만원 사이로 입력해 주세요.')
        try:
            date.fromisoformat(effective_on)
        except (ValueError, TypeError):
            raise ValidationError('적용 시작일을 확인해 주세요.')
        with transaction(self.db_path) as connection:
            row = self._require_row(connection, 'workspaces', workspace_id)
            settings = json.loads(row['settings_json'] or '{}')
            history = settings.setdefault('worker_rates', [])
            before = list(history)
            history.append({'name': name, 'amount': amount, 'effective_on': effective_on, 'saved_at': _utc_now()})
            connection.execute('UPDATE workspaces SET settings_json = ?, updated_at = ? WHERE id = ?',
                               (json.dumps(settings, ensure_ascii=False), _utc_now(), workspace_id))
            self._audit(connection, workspace_id=workspace_id, action_type='update', target_type='workspace',
                        target_id=workspace_id, actor_type='user', actor_id=None,
                        before={'worker_rates': before}, after={'worker_rates': history})

    def list_worker_rates(self, workspace_id: str) -> list[dict[str, Any]]:
        return json.loads(self.get_workspace(workspace_id).get('settings_json') or '{}').get('worker_rates', [])

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            return dict(self._require_row(connection, "workspaces", workspace_id))

    def list_workspaces(self) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces WHERE deleted_at IS NULL ORDER BY created_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def create_site(
        self,
        workspace_id: str,
        name: str,
        *,
        address_text: str | None = None,
        business_status: str | SiteStatus = SiteStatus.LEAD,
        cost_completeness: str | CostCompleteness = CostCompleteness.UNKNOWN,
        client_person_id: str | None = None,
        scope_summary: str | None = None,
        notes: str | None = None,
        planned_start_at: str | None = None,
        planned_end_at: str | None = None,
        industry_data: Mapping[str, Any] | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
        legacy_status: str | LegacyStatus = LegacyStatus.NATIVE,
        legacy_ref: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        target_id, now = _new_id(), _utc_now()
        values = {
            "id": target_id,
            "workspace_id": workspace_id,
            "name": _text(name, "name", required=True, max_length=200),
            "address_text": _text(address_text, "address_text", max_length=1_000),
            "business_status": _choice(business_status, "business_status", enum_values(SiteStatus)),
            "cost_completeness": _choice(cost_completeness, "cost_completeness", enum_values(CostCompleteness)),
            "client_person_id": client_person_id,
            "scope_summary": _text(scope_summary, "scope_summary"),
            "notes": _text(notes, "notes"),
            "planned_start_at": _iso(planned_start_at, "planned_start_at"),
            "planned_end_at": _iso(planned_end_at, "planned_end_at"),
            "industry_data_json": _json_object(industry_data, "industry_data"),
            "created_at": now,
            "updated_at": now,
            "created_by": self._created_by(actor, actor_id),
            "legacy_status": _choice(legacy_status, "legacy_status", enum_values(LegacyStatus)),
            "legacy_ref": _text(legacy_ref, "legacy_ref", max_length=2_000),
        }
        with transaction(self.db_path) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            self._require_person(connection, client_person_id, workspace_id, "client_person_id")
            connection.execute(
                """INSERT INTO sites
                (id,workspace_id,name,address_text,business_status,cost_completeness,
                 client_person_id,scope_summary,notes,planned_start_at,planned_end_at,
                 industry_data_json,created_at,updated_at,created_by,legacy_status,legacy_ref)
                VALUES (:id,:workspace_id,:name,:address_text,:business_status,:cost_completeness,
                        :client_person_id,:scope_summary,:notes,:planned_start_at,:planned_end_at,
                        :industry_data_json,:created_at,:updated_at,:created_by,:legacy_status,:legacy_ref)""",
                values,
            )
            row = self._require_row(connection, "sites", target_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=workspace_id, action_type="create", target_type="site",
                        target_id=target_id, actor_type=actor, actor_id=actor_id,
                        before=None, after=_snapshot(row))
            return dict(row)

    def get_site(self, site_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            return dict(self._require_row(connection, "sites", site_id))

    def list_sites(self, workspace_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            rows = connection.execute(
                """SELECT * FROM sites WHERE workspace_id = ? AND deleted_at IS NULL
                ORDER BY COALESCE(planned_start_at, '9999-12-31'), updated_at DESC""",
                (workspace_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def rename_imported_candidate_site(self, site_id: str, name: str, *, reason: str) -> dict[str, Any]:
        clean_name = _text(name, "name", required=True, max_length=200)
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "sites", site_id)
            if not str(before["notes"] or "").startswith("카카오톡 일정 후보에서 만든 현장"):
                raise ConflictError("only a site created from an unreviewed schedule candidate can be renamed here")
            connection.execute(
                "UPDATE sites SET name = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (clean_name, _utc_now(), site_id),
            )
            after = self._require_row(connection, "sites", site_id)
            self._audit(connection, workspace_id=after["workspace_id"], action_type="rename_candidate_site",
                        target_type="site", target_id=site_id, actor_type="user", actor_id=None,
                        before=_snapshot(before), after=_snapshot(after), reason=clean_reason)
            return dict(after)

    def create_schedule_item(
        self,
        workspace_id: str,
        schedule_type: str,
        title: str,
        start_at: str,
        summary: str,
        *,
        site_id: str | None = None,
        end_at: str | None = None,
        time_precision: str = "exact",
        customer_name: str | None = None,
        customer_contact: str | None = None,
        address_text: str | None = None,
        participants_text: str | None = None,
        notes: str | None = None,
        business_status: str = "scheduled",
        epistemic_type: str | EpistemicType = EpistemicType.DECISION,
        review_status: str | ReviewStatus = ReviewStatus.APPROVED,
        evidence_ref: str | None = None,
        confidence: float | None = None,
        source_comparison: str = "manual",
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a work schedule or estimate visit without overlap restrictions."""
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic = _choice(epistemic_type, "epistemic_type", enum_values(EpistemicType))
        review = _choice(review_status, "review_status", enum_values(ReviewStatus))
        self._validate_actor_review(actor, review)
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
            raise ValidationError("confidence must be between 0 and 1")
        kind = _choice(schedule_type, "schedule_type", ("work", "estimate_visit"))
        status = _choice(business_status, "business_status", ("scheduled", "completed", "cancelled"))
        start = _iso(start_at, "start_at")
        end = _iso(end_at, "end_at")
        if start is None:
            raise ValidationError("start_at is required")
        if end is not None and end < start:
            raise ValidationError("end_at must not be before start_at")
        if kind == "work" and not site_id:
            raise ValidationError("work schedule requires a site")
        target_id, now = _new_id(), _utc_now()
        values = {
            "id": target_id,
            "workspace_id": workspace_id,
            "site_id": site_id,
            "schedule_type": kind,
            "title": _text(title, "title", required=True, max_length=200),
            "start_at": start,
            "end_at": end,
            "time_precision": _choice(time_precision, "time_precision", ("exact", "date", "range", "approximate")),
            "summary": _text(summary, "summary", required=True, max_length=3_000),
            "customer_name": _text(customer_name, "customer_name", max_length=200),
            "customer_contact": _text(customer_contact, "customer_contact", max_length=300),
            "address_text": _text(address_text, "address_text", max_length=1_000),
            "participants_text": _text(participants_text, "participants_text", max_length=1_000),
            "notes": _text(notes, "notes", max_length=3_000),
            "business_status": status,
            "epistemic_type": epistemic,
            "review_status": review,
            "evidence_ref": _text(evidence_ref, "evidence_ref", max_length=4_000),
            "confidence": float(confidence) if confidence is not None else None,
            "source_comparison": _choice(source_comparison, "source_comparison", (
                "matched", "kakao_only", "markdown_only", "conflict", "later_confirmation", "manual"
            )),
            "created_at": now,
            "updated_at": now,
            "created_by": self._created_by(actor, actor_id),
        }
        with transaction(self.db_path) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            if site_id:
                self._require_row(connection, "sites", site_id, workspace_id=workspace_id)
            connection.execute(
                """INSERT INTO schedule_items
                (id,workspace_id,site_id,schedule_type,title,start_at,end_at,time_precision,summary,
                 customer_name,customer_contact,address_text,participants_text,notes,
                 business_status,epistemic_type,review_status,evidence_ref,confidence,source_comparison,
                 created_at,updated_at,created_by)
                VALUES (:id,:workspace_id,:site_id,:schedule_type,:title,:start_at,:end_at,:time_precision,:summary,
                        :customer_name,:customer_contact,:address_text,:participants_text,:notes,
                        :business_status,:epistemic_type,:review_status,:evidence_ref,:confidence,:source_comparison,
                        :created_at,:updated_at,:created_by)""",
                values,
            )
            row = self._require_row(connection, "schedule_items", target_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=workspace_id, action_type="create",
                        target_type="schedule_item", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=None, after=_snapshot(row))
            return dict(row)

    def list_schedule_items(
        self, workspace_id: str, *, schedule_type: str | None = None,
        review_status: str | None = None,
    ) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            params: list[Any] = [workspace_id]
            where = "workspace_id = ? AND deleted_at IS NULL"
            if schedule_type is not None:
                kind = _choice(schedule_type, "schedule_type", ("work", "estimate_visit"))
                where += " AND schedule_type = ?"
                params.append(kind)
            if review_status is not None:
                review = _choice(review_status, "review_status", enum_values(ReviewStatus))
                where += " AND review_status = ?"
                params.append(review)
            rows = connection.execute(
                f"SELECT * FROM schedule_items WHERE {where} ORDER BY start_at, created_at", params
            ).fetchall()
            return [dict(row) for row in rows]

    def get_schedule_item(self, schedule_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            return dict(self._require_row(connection, "schedule_items", schedule_id))

    def rename_pending_schedule_item(
        self, schedule_id: str, title: str, *, reason: str,
        actor_type: str | ActorType = ActorType.USER,
    ) -> dict[str, Any]:
        clean_title = _text(title, "title", required=True, max_length=200)
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "schedule_items", schedule_id)
            if before["review_status"] not in {"pending", "held"}:
                raise ConflictError("only an unapproved schedule candidate can be renamed")
            connection.execute(
                "UPDATE schedule_items SET title = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (clean_title, _utc_now(), schedule_id),
            )
            after = self._require_row(connection, "schedule_items", schedule_id)
            self._audit(connection, workspace_id=after["workspace_id"], action_type="rename_candidate",
                        target_type="schedule_item", target_id=schedule_id, actor_type=actor,
                        actor_id=None, before=_snapshot(before), after=_snapshot(after), reason=clean_reason)
            return dict(after)

    def update_pending_schedule_item(
        self,
        schedule_id: str,
        *,
        title: str,
        start_at: str,
        end_at: str | None,
        time_precision: str,
        summary: str,
        address_text: str | None,
        site_id: str | None,
        reason: str,
        actor_type: str | ActorType = ActorType.USER,
    ) -> dict[str, Any]:
        """Correct an unapproved import candidate without changing its source evidence."""
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        start = _iso(start_at, "start_at")
        end = _iso(end_at, "end_at")
        if start is None:
            raise ValidationError("start_at is required")
        if end is not None and end < start:
            raise ValidationError("end_at must not be before start_at")
        precision = _choice(time_precision, "time_precision", ("exact", "date", "range", "approximate"))
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "schedule_items", schedule_id)
            if before["review_status"] not in {"pending", "held"}:
                raise ConflictError("only an unapproved schedule candidate can be corrected")
            if before["schedule_type"] == "work" and not site_id:
                raise ValidationError("work schedule requires a site")
            if site_id:
                self._require_row(connection, "sites", site_id, workspace_id=before["workspace_id"])
            connection.execute(
                """UPDATE schedule_items
                SET title = ?, start_at = ?, end_at = ?, time_precision = ?, summary = ?,
                    address_text = ?, site_id = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (
                    _text(title, "title", required=True, max_length=200), start, end, precision,
                    _text(summary, "summary", required=True, max_length=3_000),
                    _text(address_text, "address_text", max_length=1_000), site_id,
                    _utc_now(), schedule_id,
                ),
            )
            after = self._require_row(connection, "schedule_items", schedule_id)
            self._audit(
                connection, workspace_id=after["workspace_id"], action_type="correct_candidate",
                target_type="schedule_item", target_id=schedule_id, actor_type=actor,
                actor_id=None, before=_snapshot(before), after=_snapshot(after), reason=clean_reason,
            )
            return dict(after)

    def enrich_schedule_contact(
        self, schedule_id: str, contact: str, *, reason: str,
        actor_type: str | ActorType = ActorType.SYSTEM,
    ) -> dict[str, Any]:
        """Attach source contact only when the schedule does not already have one."""
        clean_contact = _text(contact, "customer_contact", required=True, max_length=300)
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "schedule_items", schedule_id)
            if before["customer_contact"]:
                return dict(before)
            connection.execute(
                "UPDATE schedule_items SET customer_contact = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (clean_contact, _utc_now(), schedule_id),
            )
            after = self._require_row(connection, "schedule_items", schedule_id)
            self._audit(
                connection, workspace_id=after["workspace_id"], action_type="enrich_from_source",
                target_type="schedule_item", target_id=schedule_id, actor_type=actor,
                actor_id=None, before=_snapshot(before), after=_snapshot(after), reason=clean_reason,
            )
            return dict(after)

    def reschedule_item(
        self, schedule_id: str, *, start_at: str, end_at: str | None,
        time_precision: str, reason: str,
        actor_type: str | ActorType = ActorType.USER,
    ) -> dict[str, Any]:
        """Change an approved schedule while preserving before/after values in the audit log."""
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        start = _iso(start_at, "start_at")
        end = _iso(end_at, "end_at")
        if start is None:
            raise ValidationError("start_at is required")
        if end is not None and end < start:
            raise ValidationError("end_at must not be before start_at")
        precision = _choice(time_precision, "time_precision", ("exact", "date", "range", "approximate"))
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "schedule_items", schedule_id)
            if before["review_status"] != "approved":
                raise ConflictError("only an approved schedule can be rescheduled here")
            if before["business_status"] == "completed":
                raise ConflictError("a completed schedule cannot be rescheduled")
            connection.execute(
                """UPDATE schedule_items
                SET start_at = ?, end_at = ?, time_precision = ?, business_status = 'scheduled',
                    source_comparison = 'later_confirmation', updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (start, end, precision, _utc_now(), schedule_id),
            )
            after = self._require_row(connection, "schedule_items", schedule_id)
            self._audit(
                connection, workspace_id=after["workspace_id"], action_type="reschedule",
                target_type="schedule_item", target_id=schedule_id, actor_type=actor,
                actor_id=None, before=_snapshot(before), after=_snapshot(after), reason=clean_reason,
            )
            return dict(after)

    def set_schedule_cancelled(
        self, schedule_id: str, *, cancelled: bool, reason: str,
        actor_type: str | ActorType = ActorType.USER,
    ) -> dict[str, Any]:
        """Cancel or restore a schedule without deleting its original record."""
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        target_status = "cancelled" if cancelled else "scheduled"
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "schedule_items", schedule_id)
            if before["review_status"] != "approved":
                raise ConflictError("only an approved schedule can change status here")
            if before["business_status"] == "completed":
                raise ConflictError("a completed schedule cannot be cancelled or restored")
            if before["business_status"] == target_status:
                return dict(before)
            connection.execute(
                "UPDATE schedule_items SET business_status = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (target_status, _utc_now(), schedule_id),
            )
            after = self._require_row(connection, "schedule_items", schedule_id)
            self._audit(
                connection, workspace_id=after["workspace_id"],
                action_type="cancel_schedule" if cancelled else "restore_schedule",
                target_type="schedule_item", target_id=schedule_id, actor_type=actor,
                actor_id=None, before=_snapshot(before), after=_snapshot(after), reason=clean_reason,
            )
            return dict(after)

    def get_estimate_visit_result(self, schedule_id: str) -> dict[str, Any] | None:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT * FROM estimate_visit_results WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_estimate_standards(self, workspace_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            archived_filter = "" if include_archived else " AND is_active = 1"
            rows = connection.execute(
                """SELECT * FROM estimate_standards
                WHERE workspace_id = ? AND deleted_at IS NULL""" + archived_filter +
                " ORDER BY sort_order, created_at, id",
                (workspace_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_estimate_standard(
        self, workspace_id: str, *, category: str, title: str, rule_text: str,
        rationale: str | None = None, source_note: str | None = None,
        sort_order: int = 0, actor_type: str | ActorType = ActorType.USER,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        category_value = _choice(category, "category", (
            "process", "labor", "equipment", "waste", "margin", "restoration", "condition", "other"
        ))
        order = _integer(sort_order, "sort_order", minimum=0)
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            connection.execute(
                """INSERT INTO estimate_standards
                (id,workspace_id,category,title,rule_text,rationale,source_note,is_active,
                 sort_order,created_at,updated_at,created_by)
                VALUES (?,?,?,?,?,?,?,1,?,?,?,?)""",
                (target_id, workspace_id, category_value,
                 _text(title, "title", required=True, max_length=200),
                 _text(rule_text, "rule_text", required=True, max_length=10_000),
                 _text(rationale, "rationale", max_length=5_000),
                 _text(source_note, "source_note", max_length=2_000), order,
                 now, now, self._created_by(actor, None)),
            )
            row = self._require_row(connection, "estimate_standards", target_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=workspace_id, action_type="create",
                        target_type="estimate_standard", target_id=target_id,
                        actor_type=actor, actor_id=None, before=None, after=_snapshot(row),
                        reason="견적 기준 추가")
            return dict(row)

    def set_estimate_standard_active(self, workspace_id: str, standard_id: str, *, is_active: bool) -> dict[str, Any]:
        with transaction(self.db_path) as connection:
            before = self._require_row(connection, "estimate_standards", standard_id, workspace_id=workspace_id)
            connection.execute(
                "UPDATE estimate_standards SET is_active=?, updated_at=?, revision=revision+1 WHERE id=?",
                (1 if is_active else 0, _utc_now(), standard_id),
            )
            after = self._require_row(connection, "estimate_standards", standard_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=after["workspace_id"], action_type="restore" if is_active else "archive",
                        target_type="estimate_standard", target_id=standard_id,
                        actor_type="user", actor_id=None, before=_snapshot(before), after=_snapshot(after),
                        reason="견적 기준 사용 상태 변경")
            return dict(after)

    def save_estimate_visit_result(
        self, schedule_id: str, *, customer_requests: str, work_plan: str | None = None,
        labor_cost_krw: int | None = None, equipment_plan: str | None = None,
        equipment_cost_krw: int | None = None, waste_plan: str | None = None,
        waste_cost_krw: int | None = None, restoration_plan: str | None = None,
        restoration_cost_krw: int | None = None, total_quote_krw: int | None = None,
        conditions_text: str | None = None, actor_type: str | ActorType = ActorType.USER,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        amounts = {
            "labor_cost_krw": labor_cost_krw, "equipment_cost_krw": equipment_cost_krw,
            "waste_cost_krw": waste_cost_krw, "restoration_cost_krw": restoration_cost_krw,
            "total_quote_krw": total_quote_krw,
        }
        for name, amount in amounts.items():
            if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
                raise ValidationError(f"{name} must be a non-negative integer or null")
        with transaction(self.db_path) as connection:
            schedule = self._require_row(connection, "schedule_items", schedule_id)
            if schedule["schedule_type"] != "estimate_visit":
                raise ValidationError("estimate result requires an estimate visit")
            before = connection.execute(
                "SELECT * FROM estimate_visit_results WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
            now = _utc_now()
            values = (
                _text(customer_requests, "customer_requests", required=True, max_length=20_000),
                _text(work_plan, "work_plan", max_length=20_000), labor_cost_krw,
                _text(equipment_plan, "equipment_plan", max_length=10_000), equipment_cost_krw,
                _text(waste_plan, "waste_plan", max_length=10_000), waste_cost_krw,
                _text(restoration_plan, "restoration_plan", max_length=10_000), restoration_cost_krw,
                total_quote_krw, _text(conditions_text, "conditions_text", max_length=10_000),
            )
            if before:
                connection.execute(
                    """UPDATE estimate_visit_results SET
                    customer_requests=?, work_plan=?, labor_cost_krw=?, equipment_plan=?, equipment_cost_krw=?,
                    waste_plan=?, waste_cost_krw=?, restoration_plan=?, restoration_cost_krw=?,
                    total_quote_krw=?, conditions_text=?, updated_at=?, revision=revision+1 WHERE schedule_id=?""",
                    (*values, now, schedule_id),
                )
                action = "update"
            else:
                connection.execute(
                    """INSERT INTO estimate_visit_results
                    (id,workspace_id,schedule_id,customer_requests,work_plan,labor_cost_krw,
                     equipment_plan,equipment_cost_krw,waste_plan,waste_cost_krw,
                     restoration_plan,restoration_cost_krw,total_quote_krw,conditions_text,
                     created_at,updated_at,created_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (_new_id(), schedule["workspace_id"], schedule_id, *values, now, now, self._created_by(actor, None)),
                )
                action = "create"
            after = connection.execute(
                "SELECT * FROM estimate_visit_results WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
            self._audit(
                connection, workspace_id=schedule["workspace_id"], action_type=action,
                target_type="estimate_visit_result", target_id=after["id"], actor_type=actor,
                actor_id=None, before=_snapshot(before), after=_snapshot(after),
                reason="견적 방문 결과 저장",
            )
            return dict(after)

    def set_site_cost_completeness(
        self,
        site_id: str,
        completeness: str | CostCompleteness,
        *,
        reason: str,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        status = _choice(completeness, "cost_completeness", enum_values(CostCompleteness))
        clean_reason = _text(reason, "reason", required=True, max_length=2_000)
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        with transaction(self.db_path) as connection:
            before_row = self._require_row(connection, "sites", site_id)
            connection.execute(
                """UPDATE sites SET cost_completeness = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (status, _utc_now(), site_id),
            )
            after_row = self._require_row(connection, "sites", site_id)
            self._audit(connection, workspace_id=after_row["workspace_id"], action_type="update_cost_completeness",
                        target_type="site", target_id=site_id, actor_type=actor, actor_id=actor_id,
                        before=_snapshot(before_row), after=_snapshot(after_row), reason=clean_reason)
            return dict(after_row)

    def create_person(
        self,
        workspace_id: str,
        display_name: str,
        *,
        person_kind: str = "individual",
        organization_name: str | None = None,
        phone_encrypted: str | None = None,
        messenger_handle_encrypted: str | None = None,
        notes: str | None = None,
        privacy_level: str = "private",
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        target_id, now = _new_id(), _utc_now()
        values = {
            "id": target_id,
            "workspace_id": workspace_id,
            "display_name": _text(display_name, "display_name", required=True, max_length=200),
            "person_kind": _choice(person_kind, "person_kind", PERSON_KINDS),
            "organization_name": _text(organization_name, "organization_name", max_length=300),
            "phone_encrypted": _text(phone_encrypted, "phone_encrypted", max_length=4_000),
            "messenger_handle_encrypted": _text(messenger_handle_encrypted, "messenger_handle_encrypted", max_length=4_000),
            "notes": _text(notes, "notes"),
            "privacy_level": _choice(privacy_level, "privacy_level", PRIVACY_LEVELS),
            "created_at": now,
            "updated_at": now,
            "created_by": self._created_by(actor, actor_id),
        }
        with transaction(self.db_path) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            connection.execute(
                """INSERT INTO persons
                (id,workspace_id,display_name,person_kind,organization_name,phone_encrypted,
                 messenger_handle_encrypted,notes,privacy_level,created_at,updated_at,created_by)
                VALUES (:id,:workspace_id,:display_name,:person_kind,:organization_name,:phone_encrypted,
                        :messenger_handle_encrypted,:notes,:privacy_level,:created_at,:updated_at,:created_by)""",
                values,
            )
            row = self._require_row(connection, "persons", target_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=workspace_id, action_type="create", target_type="person",
                        target_id=target_id, actor_type=actor, actor_id=actor_id,
                        before=None, after=_snapshot(row))
            return dict(row)

    def list_people(self, workspace_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            rows = connection.execute(
                "SELECT * FROM persons WHERE workspace_id = ? AND deleted_at IS NULL ORDER BY display_name",
                (workspace_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_site_role(
        self,
        site_id: str,
        person_id: str,
        role_type: str,
        *,
        role_label: str | None = None,
        is_primary_contact: bool = False,
        valid_from: str | None = None,
        valid_to: str | None = None,
        epistemic_type: str | EpistemicType = EpistemicType.FACT,
        review_status: str | ReviewStatus = ReviewStatus.APPROVED,
        evidence_ref: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic, review, evidence = self._knowledge_fields(epistemic_type, review_status, evidence_ref)
        if actor == ActorType.AI.value:
            raise ValidationError("AI role classification must remain an event candidate for user review")
        self._validate_actor_review(actor, review)
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            site = self._require_row(connection, "sites", site_id)
            self._require_person(connection, person_id, site["workspace_id"], "person_id")
            connection.execute(
                """INSERT INTO site_roles
                (id,workspace_id,site_id,person_id,role_type,role_label,is_primary_contact,
                 valid_from,valid_to,created_at,updated_at,created_by,
                 epistemic_type,review_status,evidence_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, site["workspace_id"], site_id, person_id,
                 _choice(role_type, "role_type", SITE_ROLE_TYPES),
                 _text(role_label, "role_label", max_length=200), int(bool(is_primary_contact)),
                 _iso(valid_from, "valid_from"), _iso(valid_to, "valid_to"), now, now,
                 self._created_by(actor, actor_id), epistemic, review, evidence),
            )
            row = self._require_row(connection, "site_roles", target_id)
            self._audit(connection, workspace_id=site["workspace_id"], action_type="create",
                        target_type="site_role", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=None, after=_snapshot(row))
            return dict(row)

    def create_source(
        self,
        workspace_id: str,
        source_type: str,
        content_hash_sha256: str,
        storage_uri: str,
        *,
        site_id: str | None = None,
        original_name: str | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        captured_at: str | None = None,
        source_timezone: str | None = None,
        sender_person_id: str | None = None,
        receiver_person_id: str | None = None,
        parent_source_id: str | None = None,
        processing_status: str = "unprocessed",
        privacy_level: str = "sensitive",
        retention_policy: str = "keep_until_user_deletes",
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
        legacy_status: str | LegacyStatus = LegacyStatus.NATIVE,
        legacy_ref: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        digest = _text(content_hash_sha256, "content_hash_sha256", required=True, max_length=64)
        if digest is None or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise ValidationError("content_hash_sha256 must be a 64-character SHA-256 hex digest")
        target_id, now = _new_id(), _utc_now()
        values = {
            "id": target_id,
            "workspace_id": workspace_id,
            "site_id": site_id,
            "source_type": _choice(source_type, "source_type", SOURCE_TYPES),
            "original_name": _text(original_name, "original_name", max_length=500),
            "mime_type": _text(mime_type, "mime_type", max_length=200),
            "byte_size": _integer(byte_size, "byte_size", optional=True),
            "content_hash_sha256": digest.lower(),
            "storage_uri": _text(storage_uri, "storage_uri", required=True, max_length=4_000),
            "captured_at": _iso(captured_at, "captured_at"),
            "imported_at": now,
            "source_timezone": _text(source_timezone, "source_timezone", max_length=100),
            "sender_person_id": sender_person_id,
            "receiver_person_id": receiver_person_id,
            "parent_source_id": parent_source_id,
            "processing_status": _choice(processing_status, "processing_status", PROCESSING_STATUSES),
            "privacy_level": _choice(privacy_level, "privacy_level", PRIVACY_LEVELS),
            "retention_policy": _text(retention_policy, "retention_policy", required=True, max_length=200),
            "created_at": now,
            "updated_at": now,
            "created_by": self._created_by(actor, actor_id),
            "legacy_status": _choice(legacy_status, "legacy_status", enum_values(LegacyStatus)),
            "legacy_ref": _text(legacy_ref, "legacy_ref", max_length=2_000),
        }
        with transaction(self.db_path) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            if site_id is not None:
                self._require_row(connection, "sites", site_id, workspace_id=workspace_id)
            self._require_person(connection, sender_person_id, workspace_id, "sender_person_id")
            self._require_person(connection, receiver_person_id, workspace_id, "receiver_person_id")
            if parent_source_id is not None:
                self._require_row(connection, "sources", parent_source_id, workspace_id=workspace_id)
            duplicate = connection.execute(
                "SELECT id FROM sources WHERE workspace_id = ? AND content_hash_sha256 = ? AND deleted_at IS NULL",
                (workspace_id, digest.lower()),
            ).fetchone()
            if duplicate is not None:
                raise ConflictError(f"source already exists: {duplicate['id']}")
            connection.execute(
                """INSERT INTO sources
                (id,workspace_id,site_id,source_type,original_name,mime_type,byte_size,
                 content_hash_sha256,storage_uri,captured_at,imported_at,source_timezone,
                 sender_person_id,receiver_person_id,parent_source_id,processing_status,
                 privacy_level,retention_policy,created_at,updated_at,created_by,legacy_status,legacy_ref)
                VALUES (:id,:workspace_id,:site_id,:source_type,:original_name,:mime_type,:byte_size,
                        :content_hash_sha256,:storage_uri,:captured_at,:imported_at,:source_timezone,
                        :sender_person_id,:receiver_person_id,:parent_source_id,:processing_status,
                        :privacy_level,:retention_policy,:created_at,:updated_at,:created_by,:legacy_status,:legacy_ref)""",
                values,
            )
            row = self._require_row(connection, "sources", target_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=workspace_id, action_type="create", target_type="source",
                        target_id=target_id, actor_type=actor, actor_id=actor_id,
                        before=None, after=_snapshot(row))
            return dict(row)

    def create_evidence(
        self,
        source_id: str,
        evidence_type: str,
        *,
        text_start: int | None = None,
        text_end: int | None = None,
        audio_start_ms: int | None = None,
        audio_end_ms: int | None = None,
        page_number: int | None = None,
        bounding_box: Mapping[str, Any] | None = None,
        excerpt: str | None = None,
        evidence_hash_sha256: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        kind = _choice(evidence_type, "evidence_type", EVIDENCE_TYPES)
        text_from = _integer(text_start, "text_start", optional=True)
        text_to = _integer(text_end, "text_end", optional=True)
        audio_from = _integer(audio_start_ms, "audio_start_ms", optional=True)
        audio_to = _integer(audio_end_ms, "audio_end_ms", optional=True)
        page = _integer(page_number, "page_number", minimum=1, optional=True)
        if text_from is not None and text_to is not None and text_to < text_from:
            raise ValidationError("text_end must not be before text_start")
        if audio_from is not None and audio_to is not None and audio_to < audio_from:
            raise ValidationError("audio_end_ms must not be before audio_start_ms")
        if kind == "text_range" and (text_from is None or text_to is None):
            raise ValidationError("text_range requires text_start and text_end")
        if kind == "audio_range" and (audio_from is None or audio_to is None):
            raise ValidationError("audio_range requires audio_start_ms and audio_end_ms")
        if kind == "document_page" and page is None:
            raise ValidationError("document_page requires page_number")
        digest = _text(evidence_hash_sha256, "evidence_hash_sha256", max_length=64)
        if digest is not None and re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise ValidationError("evidence_hash_sha256 must be a 64-character SHA-256 hex digest")
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            source = self._require_row(connection, "sources", source_id)
            connection.execute(
                """INSERT INTO evidence
                (id,workspace_id,source_id,evidence_type,text_start,text_end,audio_start_ms,
                 audio_end_ms,page_number,bounding_box_json,excerpt,evidence_hash_sha256,created_at,created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, source["workspace_id"], source_id, kind, text_from, text_to,
                 audio_from, audio_to, page, _json_object(bounding_box, "bounding_box"),
                 _text(excerpt, "excerpt", max_length=2_000), digest.lower() if digest else None,
                 now, self._created_by(actor, actor_id)),
            )
            row = self._require_row(connection, "evidence", target_id)
            self._audit(connection, workspace_id=source["workspace_id"], action_type="create",
                        target_type="evidence", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=None, after=_snapshot(row))
            return dict(row)

    def create_insight(
        self,
        workspace_id: str,
        insight_type: str,
        title: str,
        body: str,
        *,
        site_id: str | None = None,
        evidence_id: str | None = None,
        epistemic_type: str | EpistemicType = EpistemicType.RECOMMENDATION,
        review_status: str | ReviewStatus = ReviewStatus.PENDING,
        confidence: float | None = None,
        valid_until: str | None = None,
        actor_type: str | ActorType = ActorType.AI,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic = _choice(epistemic_type, "epistemic_type", ("inference", "recommendation"))
        review = _choice(review_status, "review_status", ("pending", "approved", "held", "rejected"))
        if actor == ActorType.AI.value and review != ReviewStatus.PENDING.value:
            raise ValidationError("AI insights must remain pending until a person reviews them")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
            raise ValidationError("confidence must be between 0 and 1")
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            if site_id is not None:
                self._require_row(connection, "sites", site_id, workspace_id=workspace_id)
            if evidence_id is not None:
                evidence = self._require_row(connection, "evidence", evidence_id)
                if evidence["workspace_id"] != workspace_id:
                    raise ValidationError("evidence_id belongs to another workspace")
            connection.execute(
                """INSERT INTO insights
                (id,workspace_id,site_id,insight_type,title,body,evidence_id,epistemic_type,
                 review_status,confidence,valid_until,created_at,updated_at,created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, workspace_id, site_id, _choice(insight_type, "insight_type", INSIGHT_TYPES),
                 _text(title, "title", required=True, max_length=300),
                 _text(body, "body", required=True), evidence_id, epistemic, review,
                 float(confidence) if confidence is not None else None,
                 _iso(valid_until, "valid_until"), now, now, self._created_by(actor, actor_id)),
            )
            row = self._require_row(connection, "insights", target_id, workspace_id=workspace_id)
            self._audit(connection, workspace_id=workspace_id, action_type="create",
                        target_type="insight", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=None, after=_snapshot(row))
            return dict(row)

    def list_sources(self, workspace_id: str, *, site_id: str | None = None) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            query = "SELECT * FROM sources WHERE workspace_id = ? AND deleted_at IS NULL"
            params: list[Any] = [workspace_id]
            if site_id is not None:
                self._require_row(connection, "sites", site_id, workspace_id=workspace_id)
                query += " AND site_id = ?"
                params.append(site_id)
            query += " ORDER BY imported_at DESC"
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def list_evidence(self, workspace_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            rows = connection.execute(
                "SELECT * FROM evidence WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_insights(self, workspace_id: str, *, review_status: str | None = None) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            query = "SELECT * FROM insights WHERE workspace_id = ? AND deleted_at IS NULL"
            params: list[Any] = [workspace_id]
            if review_status is not None:
                query += " AND review_status = ?"
                params.append(_choice(review_status, "review_status", ("pending", "approved", "held", "rejected")))
            query += " ORDER BY created_at DESC"
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def _site_and_people(
        self, connection: sqlite3.Connection, site_id: str, person_fields: Mapping[str, str | None]
    ) -> sqlite3.Row:
        site = self._require_row(connection, "sites", site_id)
        for field, person_id in person_fields.items():
            self._require_person(connection, person_id, site["workspace_id"], field)
        return site

    def create_event(
        self,
        site_id: str,
        event_type: str,
        title: str,
        *,
        description: str | None = None,
        occurred_at: str | None = None,
        occurred_end_at: str | None = None,
        time_precision: str = "unknown",
        raw_time_text: str | None = None,
        business_status: str | EventStatus = EventStatus.OCCURRED,
        epistemic_type: str | EpistemicType = EpistemicType.FACT,
        review_status: str | ReviewStatus = ReviewStatus.APPROVED,
        actor_person_id: str | None = None,
        evidence_ref: str | None = None,
        supersedes_event_id: str | None = None,
        correction_reason: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic, review, evidence = self._knowledge_fields(epistemic_type, review_status, evidence_ref)
        self._validate_actor_review(actor, review, is_replacement=bool(supersedes_event_id))
        reason = _text(correction_reason, "correction_reason", max_length=2_000)
        if supersedes_event_id and not reason:
            raise ValidationError("superseding an event requires correction_reason")
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            site = self._site_and_people(connection, site_id, {"actor_person_id": actor_person_id})
            if supersedes_event_id:
                old = self._require_row(connection, "events", supersedes_event_id, workspace_id=site["workspace_id"])
                if old["site_id"] != site_id:
                    raise ValidationError("superseded event must belong to the same site")
                if connection.execute(
                    "SELECT 1 FROM events WHERE supersedes_event_id = ? AND deleted_at IS NULL", (supersedes_event_id,)
                ).fetchone():
                    raise ConflictError("event already has a successor")
            connection.execute(
                """INSERT INTO events
                (id,workspace_id,site_id,event_type,title,description,occurred_at,occurred_end_at,
                 time_precision,raw_time_text,business_status,epistemic_type,review_status,
                 actor_person_id,evidence_ref,supersedes_event_id,created_at,updated_at,created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, site["workspace_id"], site_id,
                 _text(event_type, "event_type", required=True, max_length=100),
                 _text(title, "title", required=True, max_length=300), _text(description, "description"),
                 _iso(occurred_at, "occurred_at"), _iso(occurred_end_at, "occurred_end_at"),
                 _choice(time_precision, "time_precision", TIME_PRECISIONS),
                 _text(raw_time_text, "raw_time_text", max_length=500),
                 _choice(business_status, "business_status", enum_values(EventStatus)), epistemic,
                 review, actor_person_id, evidence, supersedes_event_id, now, now,
                 self._created_by(actor, actor_id)),
            )
            row = self._require_row(connection, "events", target_id)
            self._audit(connection, workspace_id=site["workspace_id"],
                        action_type=("propose_correction" if supersedes_event_id and review not in EFFECTIVE_REVIEW_STATUSES
                                     else "supersede" if supersedes_event_id else "create"),
                        target_type="event", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=_snapshot(old) if supersedes_event_id else None,
                        after=_snapshot(row), reason=reason)
            if supersedes_event_id and review in EFFECTIVE_REVIEW_STATUSES:
                connection.execute(
                    """INSERT INTO reviews
                    (id,workspace_id,target_type,target_id,target_revision,action,reason,
                     before_json,after_json,reviewed_at,created_at)
                    VALUES (?, ?, 'event', ?, ?, 'correct', ?, ?, ?, ?, ?)""",
                    (_new_id(), site["workspace_id"], target_id, row["revision"], reason,
                     _json_snapshot(_snapshot(old)), _json_snapshot(_snapshot(row)), now, now),
                )
            return dict(row)

    def create_task(
        self,
        site_id: str,
        title: str,
        *,
        description: str | None = None,
        phase: str = "other",
        industry_phase: str | None = None,
        assignee_person_id: str | None = None,
        requested_by_person_id: str | None = None,
        planned_start_at: str | None = None,
        due_at: str | None = None,
        completed_at: str | None = None,
        priority: str = "normal",
        business_status: str | TaskStatus = TaskStatus.OPEN,
        blocked_reason: str | None = None,
        epistemic_type: str | EpistemicType = EpistemicType.FACT,
        review_status: str | ReviewStatus = ReviewStatus.APPROVED,
        evidence_ref: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic, review, evidence = self._knowledge_fields(epistemic_type, review_status, evidence_ref)
        self._validate_actor_review(actor, review)
        status = _choice(business_status, "business_status", enum_values(TaskStatus))
        blocked = _text(blocked_reason, "blocked_reason")
        completed = _iso(completed_at, "completed_at")
        if status == TaskStatus.BLOCKED.value and not blocked:
            raise ValidationError("blocked task requires blocked_reason")
        if status == TaskStatus.DONE.value and not completed:
            completed = _utc_now()
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            site = self._site_and_people(
                connection, site_id,
                {"assignee_person_id": assignee_person_id, "requested_by_person_id": requested_by_person_id},
            )
            connection.execute(
                """INSERT INTO tasks
                (id,workspace_id,site_id,title,description,phase,assignee_person_id,
                 requested_by_person_id,planned_start_at,due_at,completed_at,priority,
                 business_status,blocked_reason,epistemic_type,review_status,evidence_ref,
                 created_at,updated_at,created_by,industry_phase)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, site["workspace_id"], site_id,
                 _text(title, "title", required=True, max_length=300), _text(description, "description"),
                 _choice(phase, "phase", TASK_PHASES), assignee_person_id, requested_by_person_id,
                 _iso(planned_start_at, "planned_start_at"), _iso(due_at, "due_at"), completed,
                 _choice(priority, "priority", PRIORITIES), status, blocked, epistemic, review,
                 evidence, now, now, self._created_by(actor, actor_id),
                 _text(industry_phase, "industry_phase", max_length=100)),
            )
            row = self._require_row(connection, "tasks", target_id)
            self._audit(connection, workspace_id=site["workspace_id"], action_type="create",
                        target_type="task", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=None, after=_snapshot(row))
            return dict(row)

    @staticmethod
    def _validate_money_direction(purpose: str, direction: str) -> None:
        if purpose in REVENUE_PURPOSES and direction != MoneyDirection.INFLOW.value:
            raise ValidationError(f"{purpose} must use inflow direction")
        if purpose in COST_PURPOSES and direction != MoneyDirection.OUTFLOW.value:
            raise ValidationError(f"{purpose} must use outflow direction")
        if purpose == MoneyPurpose.DISCOUNT.value and direction != MoneyDirection.NEUTRAL.value:
            raise ValidationError("discount must use neutral direction")
        if purpose == MoneyPurpose.REFUND.value and direction != MoneyDirection.OUTFLOW.value:
            raise ValidationError("refund must use outflow direction")

    def create_money_item(
        self,
        site_id: str,
        lineage_key: str,
        title: str,
        amount_krw: int | None,
        purpose: str | MoneyPurpose,
        progression: str | MoneyProgression,
        actualness: str | Actualness,
        direction: str | MoneyDirection,
        *,
        description: str | None = None,
        epistemic_type: str | EpistemicType = EpistemicType.FACT,
        review_status: str | ReviewStatus = ReviewStatus.APPROVED,
        counterparty_person_id: str | None = None,
        occurred_at: str | None = None,
        due_at: str | None = None,
        settled_at: str | None = None,
        supersedes_money_item_id: str | None = None,
        quantity: int | float | None = None,
        unit: str | None = None,
        unit_price_krw: int | None = None,
        tax_included: bool | None = None,
        notes: str | None = None,
        evidence_ref: str | None = None,
        correction_reason: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic, review, evidence = self._knowledge_fields(epistemic_type, review_status, evidence_ref)
        self._validate_actor_review(actor, review, is_replacement=bool(supersedes_money_item_id))
        clean_lineage = _text(lineage_key, "lineage_key", required=True, max_length=200)
        clean_purpose = _choice(purpose, "purpose", enum_values(MoneyPurpose))
        clean_progression = _choice(progression, "progression", enum_values(MoneyProgression))
        clean_actualness = _choice(actualness, "actualness", enum_values(Actualness))
        clean_direction = _choice(direction, "direction", enum_values(MoneyDirection))
        self._validate_money_direction(clean_purpose, clean_direction)
        amount = _integer(amount_krw, "amount_krw", optional=True)
        clean_settled = _iso(settled_at, "settled_at")
        if clean_progression == MoneyProgression.SETTLED.value and not clean_settled:
            clean_settled = _utc_now()
        if clean_settled and clean_progression != MoneyProgression.SETTLED.value:
            raise ValidationError("settled_at requires settled progression")
        clean_quantity = _number(quantity, "quantity", optional=True)
        clean_unit = _text(unit, "unit", max_length=50)
        clean_unit_price = _integer(unit_price_krw, "unit_price_krw", optional=True)
        if any(value is not None for value in (clean_quantity, clean_unit, clean_unit_price)) and not all(
            value is not None for value in (clean_quantity, clean_unit, clean_unit_price)
        ):
            raise ValidationError("quantity, unit, and unit_price_krw must be supplied together")
        reason = _text(correction_reason, "correction_reason", max_length=2_000)
        if supersedes_money_item_id and not reason:
            raise ValidationError("superseding a money item requires correction_reason")
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            site = self._site_and_people(
                connection, site_id, {"counterparty_person_id": counterparty_person_id}
            )
            previous: sqlite3.Row | None = None
            if supersedes_money_item_id:
                previous = self._require_row(
                    connection, "money_items", supersedes_money_item_id,
                    workspace_id=site["workspace_id"],
                )
                if previous["site_id"] != site_id or previous["lineage_key"] != clean_lineage:
                    raise ValidationError("superseded money item must use the same site and lineage_key")
                if connection.execute(
                    "SELECT 1 FROM money_items WHERE supersedes_money_item_id = ? AND deleted_at IS NULL",
                    (supersedes_money_item_id,),
                ).fetchone():
                    raise ConflictError("money item already has a successor")
            try:
                connection.execute(
                    """INSERT INTO money_items
                    (id,workspace_id,site_id,lineage_key,title,description,amount_krw,purpose,
                     progression,actualness,direction,epistemic_type,review_status,
                     counterparty_person_id,occurred_at,due_at,settled_at,supersedes_money_item_id,
                     quantity,unit,unit_price_krw,tax_included,notes,evidence_ref,
                     created_at,updated_at,created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (target_id, site["workspace_id"], site_id, clean_lineage,
                     _text(title, "title", required=True, max_length=300), _text(description, "description"),
                     amount, clean_purpose, clean_progression, clean_actualness, clean_direction,
                     epistemic, review, counterparty_person_id, _iso(occurred_at, "occurred_at"),
                     _iso(due_at, "due_at"), clean_settled, supersedes_money_item_id,
                     clean_quantity, clean_unit, clean_unit_price,
                     None if tax_included is None else int(bool(tax_included)), _text(notes, "notes"),
                     evidence, now, now, self._created_by(actor, actor_id)),
                )
            except sqlite3.IntegrityError as exc:
                if "money_items.site_id, money_items.lineage_key" in str(exc):
                    raise ConflictError("lineage_key already exists; supersede its current item") from exc
                raise
            row = self._require_row(connection, "money_items", target_id)
            self._audit(connection, workspace_id=site["workspace_id"],
                        action_type=("propose_correction" if previous is not None and review not in EFFECTIVE_REVIEW_STATUSES
                                     else "supersede" if previous is not None else "create"),
                        target_type="money_item", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=_snapshot(previous), after=_snapshot(row), reason=reason)
            if previous is not None and review in EFFECTIVE_REVIEW_STATUSES:
                review_id = _new_id()
                connection.execute(
                    """INSERT INTO reviews
                    (id,workspace_id,target_type,target_id,target_revision,action,reason,
                     before_json,after_json,reviewed_at,created_at)
                    VALUES (?, ?, 'money_item', ?, ?, 'correct', ?, ?, ?, ?, ?)""",
                    (review_id, site["workspace_id"], target_id, row["revision"], reason,
                     _json_snapshot(_snapshot(previous)), _json_snapshot(_snapshot(row)), now, now),
                )
            return dict(row)

    def create_risk(
        self,
        site_id: str,
        category: str,
        title: str,
        *,
        description: str | None = None,
        severity: str = "medium",
        industry_category: str | None = None,
        likelihood: str = "possible",
        business_status: str | RiskStatus = RiskStatus.OPEN,
        estimated_extra_cost_money_item_id: str | None = None,
        estimated_delay_minutes: int | None = None,
        response_plan: str | None = None,
        owner_person_id: str | None = None,
        due_at: str | None = None,
        resolved_at: str | None = None,
        resolution_note: str | None = None,
        resolution_evidence_ref: str | None = None,
        epistemic_type: str | EpistemicType = EpistemicType.FACT,
        review_status: str | ReviewStatus = ReviewStatus.APPROVED,
        evidence_ref: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        epistemic, review, evidence = self._knowledge_fields(epistemic_type, review_status, evidence_ref)
        self._validate_actor_review(actor, review)
        status = _choice(business_status, "business_status", enum_values(RiskStatus))
        resolution = _text(resolution_note, "resolution_note")
        resolution_evidence = _text(resolution_evidence_ref, "resolution_evidence_ref", max_length=2_000)
        if status in {RiskStatus.MITIGATED.value, RiskStatus.CLOSED.value} and not (resolution and resolution_evidence):
            raise ValidationError("mitigated/closed risk requires resolution note and evidence")
        target_id, now = _new_id(), _utc_now()
        with transaction(self.db_path) as connection:
            site = self._site_and_people(connection, site_id, {"owner_person_id": owner_person_id})
            if estimated_extra_cost_money_item_id:
                money = self._require_row(
                    connection, "money_items", estimated_extra_cost_money_item_id,
                    workspace_id=site["workspace_id"],
                )
                if money["site_id"] != site_id:
                    raise ValidationError("estimated extra-cost item must belong to the same site")
            connection.execute(
                """INSERT INTO risks
                (id,workspace_id,site_id,category,title,description,severity,likelihood,
                 business_status,estimated_extra_cost_money_item_id,estimated_delay_minutes,
                 response_plan,owner_person_id,due_at,resolved_at,resolution_note,
                 resolution_evidence_ref,epistemic_type,review_status,evidence_ref,
                 created_at,updated_at,created_by,industry_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, site["workspace_id"], site_id,
                 _choice(category, "category", RISK_CATEGORIES),
                 _text(title, "title", required=True, max_length=300), _text(description, "description"),
                 _choice(severity, "severity", SEVERITIES),
                 _choice(likelihood, "likelihood", LIKELIHOODS), status,
                 estimated_extra_cost_money_item_id,
                 _integer(estimated_delay_minutes, "estimated_delay_minutes", optional=True),
                 _text(response_plan, "response_plan"), owner_person_id, _iso(due_at, "due_at"),
                 _iso(resolved_at, "resolved_at"), resolution, resolution_evidence,
                 epistemic, review, evidence, now, now, self._created_by(actor, actor_id),
                 _text(industry_category, "industry_category", max_length=100)),
            )
            row = self._require_row(connection, "risks", target_id)
            self._audit(connection, workspace_id=site["workspace_id"], action_type="create",
                        target_type="risk", target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=None, after=_snapshot(row))
            return dict(row)

    def list_for_site(self, site_id: str, record_type: str) -> list[dict[str, Any]]:
        table_by_type = {
            "role": "site_roles",
            "event": "events",
            "task": "tasks",
            "money_item": "money_items",
            "risk": "risks",
        }
        table = table_by_type.get(record_type)
        if table is None:
            raise ValidationError(f"unsupported record_type: {record_type}")
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "sites", site_id)
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE site_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
                (site_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def review_target(
        self,
        target_type: str,
        target_id: str,
        action: str | ReviewAction,
        *,
        reason: str | None = None,
        reviewer_person_id: str | None = None,
        actor_type: str | ActorType = ActorType.USER,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        table = REVIEWABLE_TABLES.get(target_type)
        if table is None:
            raise ValidationError(f"unsupported review target_type: {target_type}")
        clean_action = _choice(action, "action", enum_values(ReviewAction))
        if clean_action == ReviewAction.CORRECT.value:
            raise ValidationError(
                "corrections must create a replacement record; money corrections use supersedes_money_item_id"
            )
        clean_reason = _text(reason, "reason", max_length=2_000)
        if clean_action in {ReviewAction.HOLD.value, ReviewAction.REJECT.value, ReviewAction.REOPEN.value} and not clean_reason:
            raise ValidationError(f"{clean_action} review requires reason")
        status_by_action = {
            ReviewAction.APPROVE.value: ReviewStatus.APPROVED.value,
            ReviewAction.HOLD.value: ReviewStatus.HELD.value,
            ReviewAction.REJECT.value: ReviewStatus.REJECTED.value,
            ReviewAction.REOPEN.value: ReviewStatus.PENDING.value,
        }
        new_status = status_by_action[clean_action]
        actor = _choice(actor_type, "actor_type", enum_values(ActorType))
        if actor != ActorType.USER.value:
            raise ValidationError("only a user can make a review decision")
        now = _utc_now()
        with transaction(self.db_path) as connection:
            before_row = self._require_row(connection, table, target_id)
            workspace_id = before_row["workspace_id"]
            if before_row["legacy_status"] in {
                LegacyStatus.SUPERSEDED.value,
                LegacyStatus.REFERENCE_ONLY.value,
                LegacyStatus.EXCLUDED.value,
            }:
                raise ConflictError("inactive legacy records cannot be reviewed")
            successor_column = {
                "event": "supersedes_event_id",
                "money_item": "supersedes_money_item_id",
            }.get(target_type)
            if successor_column and connection.execute(
                f"SELECT 1 FROM {table} WHERE {successor_column} = ? AND deleted_at IS NULL",
                (target_id,),
            ).fetchone():
                raise ConflictError("superseded records cannot be reviewed; review the current version")
            self._require_person(connection, reviewer_person_id, workspace_id, "reviewer_person_id")
            if (
                new_status == ReviewStatus.APPROVED.value
                and before_row["epistemic_type"] in {
                    EpistemicType.INFERENCE.value, EpistemicType.RECOMMENDATION.value
                }
                and not before_row["evidence_ref"]
            ):
                raise ValidationError("approved inference/recommendation requires evidence_ref")
            connection.execute(
                f"UPDATE {table} SET review_status = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (new_status, now, target_id),
            )
            after_row = self._require_row(connection, table, target_id)
            review_id = _new_id()
            connection.execute(
                """INSERT INTO reviews
                (id,workspace_id,target_type,target_id,target_revision,action,reason,
                 before_json,after_json,reviewer_person_id,reviewed_at,created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (review_id, workspace_id, target_type, target_id, after_row["revision"],
                 clean_action, clean_reason, _json_snapshot(_snapshot(before_row)),
                 _json_snapshot(_snapshot(after_row)), reviewer_person_id, now, now),
            )
            self._audit(connection, workspace_id=workspace_id, action_type="review",
                        target_type=target_type, target_id=target_id, actor_type=actor,
                        actor_id=actor_id, before=_snapshot(before_row), after=_snapshot(after_row),
                        reason=clean_reason)
            return dict(connection.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone())

    def pending_reviews(self, workspace_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            pending: list[dict[str, Any]] = []
            for target_type, table in REVIEWABLE_TABLES.items():
                current_filter = ""
                if target_type == "event":
                    current_filter = (
                        " AND NOT EXISTS (SELECT 1 FROM events AS child "
                        "WHERE child.supersedes_event_id = current.id AND child.deleted_at IS NULL)"
                    )
                elif target_type == "money_item":
                    current_filter = (
                        " AND NOT EXISTS (SELECT 1 FROM money_items AS child "
                        "WHERE child.supersedes_money_item_id = current.id AND child.deleted_at IS NULL)"
                    )
                rows = connection.execute(
                    f"""SELECT current.* FROM {table} AS current
                    WHERE workspace_id = ? AND review_status IN ('pending','held')
                      AND deleted_at IS NULL
                      AND legacy_status NOT IN ('superseded','reference_only','excluded')
                      {current_filter} ORDER BY created_at""",
                    (workspace_id,),
                ).fetchall()
                pending.extend({"target_type": target_type, **dict(row)} for row in rows)
            pending.sort(key=lambda item: (item.get("created_at") or "", item["target_type"], item["id"]))
            return pending

    def site_financial_summary(self, site_id: str) -> FinancialSummary:
        with closing(connect(self.db_path)) as connection:
            site = self._require_row(connection, "sites", site_id)
            rows = connection.execute(
                """SELECT m.* FROM money_items AS m
                WHERE m.site_id = ? AND m.deleted_at IS NULL
                  AND m.legacy_status NOT IN ('superseded','reference_only','excluded')
                ORDER BY m.created_at""",
                (site_id,),
            ).fetchall()

        # A pending correction must not silently erase the last approved value.
        # Keep the chain head for unresolved-review detection, then walk backwards
        # to the newest human-approved row for confirmed calculations.
        by_id = {row["id"]: row for row in rows}
        superseded_ids = {
            row["supersedes_money_item_id"]
            for row in rows
            if row["supersedes_money_item_id"] is not None
        }
        heads = [row for row in rows if row["id"] not in superseded_ids]
        effective: list[sqlite3.Row] = []
        for head in heads:
            candidate: sqlite3.Row | None = head
            while candidate is not None and candidate["review_status"] not in EFFECTIVE_REVIEW_STATUSES:
                parent_id = candidate["supersedes_money_item_id"]
                candidate = by_id.get(parent_id) if parent_id else None
            if candidate is not None and candidate["progression"] != MoneyProgression.VOID.value:
                effective.append(candidate)
        revenue_rows = [
            row for row in effective
            if row["purpose"] in REVENUE_PURPOSES
            and row["direction"] == MoneyDirection.INFLOW.value
            and row["progression"] in AGREED_REVENUE_PROGRESSIONS
        ]
        discount_rows = [
            row for row in effective
            if row["purpose"] == MoneyPurpose.DISCOUNT.value
            and row["progression"] in AGREED_REVENUE_PROGRESSIONS
        ]
        revenue = None
        has_base_quote = any(row["purpose"] == MoneyPurpose.BASE_QUOTE.value for row in revenue_rows)
        if revenue_rows and has_base_quote:
            if all(row["amount_krw"] is not None for row in revenue_rows + discount_rows):
                revenue = sum(int(row["amount_krw"]) for row in revenue_rows) - sum(
                    int(row["amount_krw"]) for row in discount_rows
                )

        all_cost_rows = [
            row for row in effective
            if row["purpose"] in COST_PURPOSES and row["direction"] == MoneyDirection.OUTFLOW.value
        ]
        unresolved = [
            row for row in heads
            if row["purpose"] in COST_PURPOSES
            and row["direction"] == MoneyDirection.OUTFLOW.value
            if row["review_status"] in {ReviewStatus.PENDING.value, ReviewStatus.HELD.value}
            or row["amount_krw"] is None
        ]
        effective_costs = all_cost_rows
        costs_known = (
            site["cost_completeness"] == CostCompleteness.COMPLETE.value
            and not unresolved
            and all(row["amount_krw"] is not None for row in effective_costs)
        )
        cost: int | None = sum(int(row["amount_krw"]) for row in effective_costs) if costs_known else None

        cost_basis: str | None = None
        if costs_known:
            bases = {row["actualness"] for row in effective_costs}
            if not bases:
                cost_basis = "none"
            elif bases == {Actualness.ACTUAL.value}:
                cost_basis = "actual"
            elif bases == {Actualness.ESTIMATED.value}:
                cost_basis = "estimated"
            else:
                cost_basis = "mixed"

        if revenue is None:
            margin, margin_status = None, "unknown_revenue"
        elif cost is None:
            margin, margin_status = None, "unknown_costs"
        else:
            margin, margin_status = revenue - cost, "known"
        return FinancialSummary(
            site_id=site_id,
            agreed_revenue_krw=revenue,
            cost_krw=cost,
            cost_basis=cost_basis,
            margin_krw=margin,
            margin_status=margin_status,
            unresolved_cost_items=len(unresolved),
        )

    def dashboard(self, workspace_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            row = connection.execute(
                """SELECT
                (SELECT COUNT(*) FROM sites WHERE workspace_id = ? AND deleted_at IS NULL) AS sites,
                (SELECT COUNT(*) FROM tasks WHERE workspace_id = ? AND deleted_at IS NULL
                    AND business_status IN ('open','in_progress','blocked')) AS active_tasks,
                (SELECT COUNT(*) FROM risks WHERE workspace_id = ? AND deleted_at IS NULL
                    AND business_status IN ('open','monitoring','occurred')) AS active_risks,
                ((SELECT COUNT(*) FROM events WHERE workspace_id = ? AND deleted_at IS NULL
                    AND review_status IN ('pending','held')
                    AND legacy_status NOT IN ('superseded','reference_only','excluded')
                    AND NOT EXISTS (SELECT 1 FROM events AS child
                        WHERE child.supersedes_event_id = events.id AND child.deleted_at IS NULL))
                 +(SELECT COUNT(*) FROM tasks WHERE workspace_id = ? AND deleted_at IS NULL
                    AND review_status IN ('pending','held')
                    AND legacy_status NOT IN ('superseded','reference_only','excluded'))
                 +(SELECT COUNT(*) FROM money_items WHERE workspace_id = ? AND deleted_at IS NULL
                    AND review_status IN ('pending','held')
                    AND legacy_status NOT IN ('superseded','reference_only','excluded')
                    AND NOT EXISTS (SELECT 1 FROM money_items AS child
                        WHERE child.supersedes_money_item_id = money_items.id AND child.deleted_at IS NULL))
                 +(SELECT COUNT(*) FROM risks WHERE workspace_id = ? AND deleted_at IS NULL
                    AND review_status IN ('pending','held')
                    AND legacy_status NOT IN ('superseded','reference_only','excluded'))) AS pending_review""",
                (workspace_id,) * 7,
            ).fetchone()
            return dict(row)

    def audit_log(self, workspace_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = _integer(limit, "limit", minimum=1)
        if safe_limit > 1_000:
            raise ValidationError("limit cannot exceed 1000")
        with closing(connect(self.db_path)) as connection:
            self._require_row(connection, "workspaces", workspace_id)
            rows = connection.execute(
                """SELECT * FROM audit_events WHERE workspace_id = ?
                ORDER BY occurred_at DESC, id DESC LIMIT ?""",
                (workspace_id, safe_limit),
            ).fetchall()
            return [dict(row) for row in rows]
