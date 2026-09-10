"""Core domain vocabulary for Field Brain.

The module deliberately contains no database path or user data.  Values that are
easy to accidentally mix together (knowledge, human review, and business
progress) are represented by separate enums.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    """A Python 3.10-compatible string enum."""

    def __str__(self) -> str:
        return self.value


class EpistemicType(StrEnum):
    FACT = "fact"
    CLAIM = "claim"
    INFERENCE = "inference"
    DECISION = "decision"
    RECOMMENDATION = "recommendation"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CORRECTED = "corrected"
    HELD = "held"
    REJECTED = "rejected"


class SiteStatus(StrEnum):
    LEAD = "lead"
    ESTIMATING = "estimating"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class CostCompleteness(StrEnum):
    """Whether every cost lineage needed for a margin is represented."""

    UNKNOWN = "unknown"
    PARTIAL = "partial"
    COMPLETE = "complete"


class EventStatus(StrEnum):
    PLANNED = "planned"
    OCCURRED = "occurred"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class RiskStatus(StrEnum):
    OPEN = "open"
    MONITORING = "monitoring"
    MITIGATED = "mitigated"
    OCCURRED = "occurred"
    CLOSED = "closed"


class MoneyPurpose(StrEnum):
    BASE_QUOTE = "base_quote"
    ADDON_QUOTE = "addon_quote"
    DISCOUNT = "discount"
    COMMISSION = "commission"
    LABOR_COST = "labor_cost"
    EQUIPMENT_COST = "equipment_cost"
    WASTE_COST = "waste_cost"
    MATERIAL_COST = "material_cost"
    TRANSPORT_COST = "transport_cost"
    OUTSOURCE_COST = "outsource_cost"
    SCRAP_INCOME = "scrap_income"
    DEPOSIT = "deposit"
    RECEIVABLE = "receivable"
    SETTLEMENT = "settlement"
    REFUND = "refund"
    TAX = "tax"
    OTHER = "other"


class MoneyProgression(StrEnum):
    CANDIDATE = "candidate"
    OFFERED = "offered"
    AGREED = "agreed"
    CLAIMED = "claimed"
    CONFIRMED = "confirmed"
    SETTLED = "settled"
    VOID = "void"


class Actualness(StrEnum):
    ESTIMATED = "estimated"
    ACTUAL = "actual"


class MoneyDirection(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    NEUTRAL = "neutral"


class ActorType(StrEnum):
    USER = "user"
    AI = "ai"
    SYSTEM = "system"
    MIGRATION = "migration"


class LegacyStatus(StrEnum):
    NATIVE = "native"
    MIGRATED_CONFIRMED = "migrated_confirmed"
    MIGRATED_REVIEW_REQUIRED = "migrated_review_required"
    SUPERSEDED = "superseded"
    REFERENCE_ONLY = "reference_only"
    EXCLUDED = "excluded"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    CORRECT = "correct"
    HOLD = "hold"
    REJECT = "reject"
    REOPEN = "reopen"


REVENUE_PURPOSES = frozenset(
    {
        MoneyPurpose.BASE_QUOTE.value,
        MoneyPurpose.ADDON_QUOTE.value,
        MoneyPurpose.SCRAP_INCOME.value,
    }
)

COST_PURPOSES = frozenset(
    {
        MoneyPurpose.COMMISSION.value,
        MoneyPurpose.LABOR_COST.value,
        MoneyPurpose.EQUIPMENT_COST.value,
        MoneyPurpose.WASTE_COST.value,
        MoneyPurpose.MATERIAL_COST.value,
        MoneyPurpose.TRANSPORT_COST.value,
        MoneyPurpose.OUTSOURCE_COST.value,
        MoneyPurpose.TAX.value,
        MoneyPurpose.OTHER.value,
    }
)

AGREED_REVENUE_PROGRESSIONS = frozenset(
    {
        MoneyProgression.AGREED.value,
        MoneyProgression.CLAIMED.value,
        MoneyProgression.CONFIRMED.value,
        MoneyProgression.SETTLED.value,
    }
)

EFFECTIVE_REVIEW_STATUSES = frozenset(
    {ReviewStatus.APPROVED.value, ReviewStatus.CORRECTED.value}
)


@dataclass(frozen=True)
class FinancialSummary:
    """A calculated view; no summary amount is stored as a second ledger."""

    site_id: str
    agreed_revenue_krw: int | None
    cost_krw: int | None
    cost_basis: str | None
    margin_krw: int | None
    margin_status: str
    unresolved_cost_items: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "agreed_revenue_krw": self.agreed_revenue_krw,
            "cost_krw": self.cost_krw,
            "cost_basis": self.cost_basis,
            "margin_krw": self.margin_krw,
            "margin_status": self.margin_status,
            "unresolved_cost_items": self.unresolved_cost_items,
        }


def enum_values(enum_type: type[Enum]) -> tuple[str, ...]:
    return tuple(str(member.value) for member in enum_type)
