"""Read-only preflight for legacy sites versus the private online snapshot.

An address match proposes a correspondence, never proves the same contract.
This module neither imports records nor calls the online service.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable, Mapping, Any


def _address(value: Any) -> str:
    # Preserve numbers, punctuation, unit and building names. No fuzzy matching.
    return " ".join(value.split()) if isinstance(value, str) else ""


def plan_site_links(
    local_sites: Iterable[Mapping[str, Any]],
    schedule_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    local = [dict(row) for row in local_sites if not row.get("deleted_at")]
    if any(not isinstance(s.get("id"), str) or not s["id"] for s in local):
        raise ValueError("Every local site needs a stable id")
    if len({s["id"] for s in local}) != len(local):
        raise ValueError("Duplicate local site ids")
    group_addresses: dict[str, set[str]] = defaultdict(set)
    incomplete_groups: set[str] = set()
    for row in schedule_rows:
        group = row.get("site")
        if not isinstance(group, str) or not group:
            raise ValueError("Every online schedule needs its stable group id")
        address = _address(row.get("address"))
        if address:
            group_addresses[group].add(address)
        else:
            incomplete_groups.add(group)
    address_groups: dict[str, set[str]] = defaultdict(set)
    for group, addresses in group_addresses.items():
        for address in addresses:
            address_groups[address].add(group)
    local_counts = Counter(_address(s.get("address_text")) for s in local)
    result = []
    for site in sorted(local, key=lambda s: s["id"]):
        address = _address(site.get("address_text"))
        matches = sorted(address_groups.get(address, ())) if address else []
        if not address:
            reason = "missing_local_address"
        elif local_counts[address] > 1:
            reason = "multiple_local_sites_at_address"
        elif not matches:
            reason = "no_exact_address_match"
        elif len(matches) > 1:
            reason = "multiple_online_groups_at_address"
        elif len(group_addresses[matches[0]]) != 1 or matches[0] in incomplete_groups:
            reason = "inconsistent_online_group"
        else:
            reason = "unique_address_candidate"
        result.append({
            "local_site_id": site["id"],
            "online_group_candidates": matches,
            "reason": reason,
            "automatic_transfer_allowed": False,
        })
    return result


def main() -> None:
    import argparse
    import json
    import sqlite3
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    # mode=ro prevents accidental creation, migration or alteration of the DB.
    from contextlib import closing
    with closing(sqlite3.connect(Path(args.database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        local = [dict(r) for r in db.execute(
            "SELECT id,address_text,deleted_at FROM sites WHERE workspace_id = ?",
            (args.workspace,),
        )]
    rows = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    plan = plan_site_links(local, rows)
    # Default report is counts only: no customer addresses, contacts, or raw chat.
    print(json.dumps({"local_sites": len(plan), "outcomes": dict(Counter(
        item["reason"] for item in plan)), "writes": 0,
        "automatic_transfer_allowed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
