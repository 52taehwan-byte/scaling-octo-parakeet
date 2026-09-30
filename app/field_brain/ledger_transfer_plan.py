"""Read-only classification before copying any legacy costs online.

Candidates still require source verification, site/contract matching and online
duplicate checks. Neither address equality nor this report authorizes a write.
"""
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Mapping

from .analytics import current_money_rows

CATEGORIES = {
    "labor_cost": "인건비", "equipment_cost": "외주·장비비",
    "outsource_cost": "외주·장비비", "waste_cost": "폐기물 처리비",
    "material_cost": "자재비", "transport_cost": "주유·식비·음료비",
}


def plan_cost_transfer(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Retain revision ids while selecting only unambiguous actual-cost metadata."""
    rows = [dict(r) for r in rows if not r.get("deleted_at")]
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate money record ids")
    children: Counter = Counter()
    for row in rows:
        parent = row.get("supersedes_money_item_id")
        if parent:
            if parent not in by_id or any(row.get(k) != by_id[parent].get(k)
                    for k in ("workspace_id", "site_id", "lineage_key")):
                raise ValueError("Missing or cross-ledger revision parent")
            children[parent] += 1
            if children[parent] > 1:
                raise ValueError("Branched money history")
        visited = {row["id"]}
        while parent:
            if parent in visited:
                raise ValueError("Cyclic money history")
            visited.add(parent)
            if parent not in by_id:
                raise ValueError("Missing revision parent")
            parent = by_id[parent].get("supersedes_money_item_id")
    heads, effective = current_money_rows(rows)
    current = {r["chain_head_id"]: r for r in effective}
    items = []
    for head in heads:
        row = current.get(head["id"])
        category = CATEGORIES.get(row.get("purpose")) if row else None
        reason = "candidate_requires_link_and_duplicate_checks"
        if row is None:
            reason = "not_effective_or_void"
        elif row.get("has_pending_correction"):
            reason = "unresolved_correction"
        elif category is None:
            reason = "not_supported_operating_cost"
        elif row.get("direction") != "outflow" or row.get("actualness") != "actual":
            reason = "not_actual_outflow"
        elif row.get("progression") not in {"confirmed", "settled"}:
            reason = "not_confirmed_cost"
        elif type(row.get("amount_krw")) is not int or row["amount_krw"] <= 0:
            reason = "missing_or_invalid_amount"
        elif not row.get("evidence_ref"):
            reason = "missing_source_reference"
        else:
            try:
                if not row.get("occurred_at"):
                    raise ValueError()
                occurred = datetime.fromisoformat(row["occurred_at"])
                if occurred.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError):
                reason = "missing_or_ambiguous_occurrence_date"
        history = []
        ancestor = head
        while ancestor:
            history.append(ancestor["id"])
            ancestor = by_id.get(ancestor.get("supersedes_money_item_id"))
        items.append({"head_id": head["id"],
            "effective_id": row["id"] if row else None,
            "local_site_id": head.get("site_id"), "revision_ids": history,
            "category": category, "reason": reason,
            "automatic_transfer_allowed": False})
    return {"items": items, "raw_revision_count": len(rows),
            "outcomes": dict(Counter(i["reason"] for i in items)), "writes": 0}
