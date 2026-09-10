"""Calculated business views built from the single money ledger.

The functions in this module never write summary amounts back to SQLite.  They
turn the versioned money rows into values the UI can display without confusing
an unknown amount with zero or counting an old correction twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import (
    AGREED_REVENUE_PROGRESSIONS,
    COST_PURPOSES,
    EFFECTIVE_REVIEW_STATUSES,
    MoneyDirection,
    MoneyProgression,
    MoneyPurpose,
    ReviewStatus,
)


CUSTOMER_QUOTE_PURPOSES = {
    MoneyPurpose.BASE_QUOTE.value,
    MoneyPurpose.ADDON_QUOTE.value,
}

# Current product scope excludes brokerage fees; originals remain in the ledger.
OPERATING_COST_PURPOSES = set(COST_PURPOSES) - {"commission"}

# A claim is already based on an agreement, even if the cash has not arrived.
CUSTOMER_AMOUNT_PROGRESSIONS = set(AGREED_REVENUE_PROGRESSIONS) | {
    MoneyProgression.CLAIMED.value,
}

RECEIPT_PURPOSES = {
    MoneyPurpose.DEPOSIT.value,
    MoneyPurpose.SETTLEMENT.value,
}


def current_money_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return chain heads and their newest effective human-confirmed versions.

    A pending correction is a head, but calculations keep using the previous
    approved value until the correction is reviewed.  Rejected corrections
    similarly fall back to the last effective value.  Old revisions are not
    returned as separate current ledger lines.
    """

    copied = [dict(row) for row in rows]
    by_id = {row["id"]: row for row in copied}
    superseded_ids = {
        row.get("supersedes_money_item_id")
        for row in copied
        if row.get("supersedes_money_item_id")
    }
    heads = [row for row in copied if row["id"] not in superseded_ids]
    effective: list[dict[str, Any]] = []

    for head in heads:
        candidate: dict[str, Any] | None = head
        visited: set[str] = set()
        while candidate is not None:
            candidate_id = str(candidate["id"])
            if candidate_id in visited:
                # The database guards normal chains; this protects imported or
                # externally modified databases from looping forever.
                candidate = None
                break
            visited.add(candidate_id)
            if candidate.get("review_status") in EFFECTIVE_REVIEW_STATUSES:
                break
            parent_id = candidate.get("supersedes_money_item_id")
            candidate = by_id.get(parent_id) if parent_id else None

        if candidate is not None and candidate.get("progression") != MoneyProgression.VOID.value:
            current = dict(candidate)
            current["chain_head_id"] = head["id"]
            current["has_pending_correction"] = head.get("review_status") in {
                ReviewStatus.PENDING.value,
                ReviewStatus.HELD.value,
            }
            effective.append(current)

    heads.sort(key=lambda row: (row.get("created_at") or "", row["id"]), reverse=True)
    effective.sort(key=lambda row: (row.get("created_at") or "", row["id"]), reverse=True)
    return heads, effective


def _sum_or_unknown(rows: list[Mapping[str, Any]]) -> int | None:
    if not rows:
        return None
    if any(row.get("amount_krw") is None for row in rows):
        return None
    return sum(int(row["amount_krw"]) for row in rows)


def site_financial_breakdown(
    money_rows: Iterable[Mapping[str, Any]],
    *,
    cost_completeness: str,
) -> dict[str, int | None]:
    """Build the six money numbers used by the first Field Brain UI.

    ``estimated_cost`` is the current execution-cost forecast.  It may mix
    estimates with already-confirmed actual costs, but is shown only when the
    user marked the site's cost scope complete and every current cost is known.
    ``actual_cost`` and ``actual_margin`` are cash/actual-to-date values and are
    therefore allowed to remain unknown while work is in progress.
    """

    heads, effective = current_money_rows(money_rows)

    customer_rows = [
        row
        for row in effective
        if row.get("purpose") in CUSTOMER_QUOTE_PURPOSES
        and row.get("direction") == MoneyDirection.INFLOW.value
        and row.get("progression") in CUSTOMER_AMOUNT_PROGRESSIONS
    ]
    discount_rows = [
        row
        for row in effective
        if row.get("purpose") == MoneyPurpose.DISCOUNT.value
        and row.get("progression") in CUSTOMER_AMOUNT_PROGRESSIONS
    ]
    customer_total = _sum_or_unknown(customer_rows)
    discount_total = _sum_or_unknown(discount_rows)
    agreed_customer_amount: int | None
    if customer_total is None:
        agreed_customer_amount = None
    elif discount_rows and discount_total is None:
        agreed_customer_amount = None
    else:
        agreed_customer_amount = customer_total - (discount_total or 0)

    cost_rows = [
        row
        for row in effective
        if row.get("purpose") in OPERATING_COST_PURPOSES
        and row.get("direction") == MoneyDirection.OUTFLOW.value
    ]
    unresolved_cost_heads = [
        row
        for row in heads
        if row.get("purpose") in OPERATING_COST_PURPOSES
        and row.get("direction") == MoneyDirection.OUTFLOW.value
        and (
            row.get("review_status") in {ReviewStatus.PENDING.value, ReviewStatus.HELD.value}
            or row.get("amount_krw") is None
        )
    ]
    forecast_cost = _sum_or_unknown(cost_rows)
    estimated_cost = (
        forecast_cost
        if cost_completeness == "complete" and not unresolved_cost_heads
        else None
    )

    actual_cost_rows = [row for row in cost_rows if row.get("actualness") == "actual"]
    actual_cost = _sum_or_unknown(actual_cost_rows)

    scrap_rows = [
        row
        for row in effective
        if row.get("purpose") == MoneyPurpose.SCRAP_INCOME.value
        and row.get("direction") == MoneyDirection.INFLOW.value
        and row.get("progression") in CUSTOMER_AMOUNT_PROGRESSIONS
    ]
    scrap_total = _sum_or_unknown(scrap_rows)
    forecast_revenue = agreed_customer_amount
    if forecast_revenue is not None:
        if scrap_rows and scrap_total is None:
            forecast_revenue = None
        else:
            forecast_revenue += scrap_total or 0
    estimated_margin = (
        forecast_revenue - estimated_cost
        if forecast_revenue is not None and estimated_cost is not None
        else None
    )

    receipt_rows = [
        row
        for row in effective
        if row.get("purpose") in RECEIPT_PURPOSES
        and row.get("direction") == MoneyDirection.INFLOW.value
        and row.get("progression")
        in {MoneyProgression.CONFIRMED.value, MoneyProgression.SETTLED.value}
        and row.get("actualness") == "actual"
    ]
    receipts = _sum_or_unknown(receipt_rows)
    confirmed_receipts = receipts if receipts is not None else (0 if not receipt_rows else None)
    receivable = (
        max(agreed_customer_amount - confirmed_receipts, 0)
        if agreed_customer_amount is not None and confirmed_receipts is not None
        else None
    )

    actual_scrap_rows = [
        row
        for row in scrap_rows
        if row.get("actualness") == "actual"
        and row.get("progression")
        in {MoneyProgression.CONFIRMED.value, MoneyProgression.SETTLED.value}
    ]
    actual_scrap = _sum_or_unknown(actual_scrap_rows)
    actual_income: int | None
    cash_parts_present = bool(receipt_rows or actual_scrap_rows)
    if not cash_parts_present:
        actual_income = None
    elif confirmed_receipts is None or (actual_scrap_rows and actual_scrap is None):
        actual_income = None
    else:
        actual_income = confirmed_receipts + (actual_scrap or 0)
    actual_margin = (
        actual_income - actual_cost
        if actual_income is not None and actual_cost is not None
        else None
    )

    return {
        "agreed_customer_amount": agreed_customer_amount,
        "estimated_cost": estimated_cost,
        "actual_cost": actual_cost,
        "estimated_margin": estimated_margin,
        "actual_margin": actual_margin,
        "receivable": receivable,
    }


def aggregate_financial_breakdowns(
    breakdowns: Iterable[Mapping[str, int | None]],
) -> dict[str, int | None]:
    """Sum the values that are currently knowable across sites.

    The dashboard is explicitly a confirmed-value subtotal.  If no site has a
    knowable value for a metric it remains ``None`` rather than becoming zero.
    """

    rows = list(breakdowns)
    keys = (
        "agreed_customer_amount",
        "estimated_cost",
        "actual_cost",
        "estimated_margin",
        "actual_margin",
        "receivable",
    )
    result: dict[str, int | None] = {}
    for key in keys:
        values = [row.get(key) for row in rows if row.get(key) is not None]
        result[key] = sum(int(value) for value in values) if values else None
    return result
