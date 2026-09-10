"""Read-only preview adapter for the retired Codex static MVP.

The old JavaScript is treated as untrusted text and is never executed.  This
module emits review candidates only; it does not write to the Field Brain DB.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SITE_RE = re.compile(
    r'\{\s*id:\s*"(?P<legacy_id>[^"]+)"\s*,\s*name:\s*"(?P<name>[^"]+)"\s*,'
    r'\s*address:\s*"(?P<address>[^"]*)"\s*,\s*status:\s*"(?P<status>[^"]+)"\s*,'
    r'\s*directDays:\s*(?P<days>null|\d+)\s*,\s*quote:\s*(?P<quote>null|\d+)\s*,'
    r'\s*fee:\s*(?P<fee>null|\d+)\s*,\s*executionCost:\s*(?P<cost>null|\d+)\s*,'
    r'\s*scope:\s*\[(?P<scope>.*?)\]\s*,\s*records:\s*\[(?P<records>.*?)\]\s*\}',
    re.DOTALL,
)
RECORD_RE = re.compile(
    r'rec\("(?P<date>\d{4}-\d{2}-\d{2})","(?P<type>[^"]+)",'
    r'"(?P<raw>(?:\\.|[^"\\])*)","(?P<status>[^"]+)"'
    r'(?:,(?P<amount>null|\d+),"(?P<money_kind>[^"]+)")?\)',
    re.DOTALL,
)
STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"')


def _integer(raw: str) -> int | None:
    return None if raw == "null" else int(raw)


def _decode(raw: str) -> str:
    return json.loads(f'"{raw}"')


def _money_candidate(*, origin: str, purpose: str, amount: int | None, actualness: str,
                     progression: str, direction: str, title: str) -> dict[str, Any]:
    legacy_amount = amount
    if origin == "site_summary" and amount == 0:
        # The retired UI used `fee || 0`, so a stored zero cannot prove that a
        # zero fee was explicitly confirmed.
        amount = None
    return {
        "candidate_type": "money_item",
        "origin": origin,
        "title": title,
        "purpose": purpose,
        "amount_krw": amount,
        "legacy_amount_krw": legacy_amount,
        "legacy_zero_requires_confirmation": origin == "site_summary" and legacy_amount == 0,
        "actualness": actualness,
        "progression": progression,
        "direction": direction,
        "epistemic_status": "claim" if actualness == "actual" else "inference",
        "review_status": "pending",
        "legacy_status": "migrated_review_required",
    }


def _record_candidate(match: re.Match[str]) -> dict[str, Any]:
    record_type = match.group("type")
    legacy_status = match.group("status")
    raw = _decode(match.group("raw"))
    amount = _integer(match.group("amount")) if match.group("amount") else None
    money_kind = match.group("money_kind") or "none"
    common = {
        "origin": "record",
        "occurred_on": match.group("date"),
        "title": raw,
        "source_excerpt": raw,
        "legacy_record_type": record_type,
        "legacy_review_label": legacy_status,
        "review_status": "pending",
        "legacy_status": "migrated_review_required",
        "epistemic_status": "inference" if legacy_status == "estimated" else "claim",
    }
    if record_type == "risk":
        return {**common, "candidate_type": "risk", "category": "other", "severity": "medium"}
    if money_kind != "none" or amount is not None:
        mapping = {
            "customer_quote": ("base_quote", "estimated", "offered", "inflow"),
            "actual_cost": ("other", "actual", "confirmed", "outflow"),
            "expected_cost": ("other", "estimated", "candidate", "outflow"),
        }
        purpose, actualness, progression, direction = mapping.get(
            money_kind, ("other", "estimated", "candidate", "neutral")
        )
        if money_kind == "actual_cost" and "폐기물" in raw:
            purpose = "waste_cost"
        elif money_kind == "actual_cost" and "마루" in raw:
            purpose = "outsource_cost"
        return {
            **common,
            "candidate_type": "money_item",
            "purpose": purpose,
            "amount_krw": amount,
            "actualness": actualness,
            "progression": progression,
            "direction": direction,
        }
    return {**common, "candidate_type": "event", "event_type": record_type}


def preview_codex_static_mvp(source_path: str | Path) -> dict[str, Any]:
    """Parse the known seed shape without evaluating JavaScript."""
    path = Path(source_path).resolve()
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8-sig")
    sites: list[dict[str, Any]] = []

    for site_match in SITE_RE.finditer(text):
        quote = _integer(site_match.group("quote"))
        fee = _integer(site_match.group("fee"))
        cost = _integer(site_match.group("cost"))
        candidates = [_record_candidate(item) for item in RECORD_RE.finditer(site_match.group("records"))]
        summary_candidates = [
            _money_candidate(origin="site_summary", purpose="base_quote", amount=quote,
                             actualness="estimated", progression="candidate", direction="inflow",
                             title="과거 화면 상단의 고객 견적 요약"),
            _money_candidate(origin="site_summary", purpose="commission", amount=fee,
                             actualness="estimated", progression="candidate", direction="outflow",
                             title="과거 화면 상단의 수수료 요약"),
            _money_candidate(origin="site_summary", purpose="other", amount=cost,
                             actualness="estimated", progression="candidate", direction="outflow",
                             title="과거 화면 상단의 실행비 요약"),
        ]
        # A legacy 0 may mean a real zero or a default. Never auto-confirm it.
        duplicate_amounts = sorted(
            amount for amount in {quote, fee, cost}
            if amount is not None and any(c.get("amount_krw") == amount for c in candidates)
        )
        sites.append({
            "legacy_id": site_match.group("legacy_id"),
            "name": site_match.group("name"),
            "address": site_match.group("address"),
            "legacy_status": site_match.group("status"),
            "direct_days_claimed": _integer(site_match.group("days")),
            "scope_claims": [_decode(value) for value in STRING_RE.findall(site_match.group("scope"))],
            "review_status": "pending",
            "migration_status": "preview_only",
            "summary_candidates": summary_candidates,
            "record_candidates": candidates,
            "duplicate_amounts_krw": duplicate_amounts,
            "warnings": [
                "과거 화면의 confirmed 표시는 새 장부의 사람 확인으로 승계하지 않습니다.",
                "요약 금액과 사건 금액이 같으면 중복 가능성이 있으므로 하나만 선택해야 합니다.",
                "0원은 과거 기본값일 수 있으므로 실제 0원으로 자동 확정하지 않습니다.",
            ],
        })

    if not sites:
        raise ValueError("지원하는 정적 MVP seedData 현장을 찾지 못했습니다.")

    total_candidates = sum(len(s["summary_candidates"]) + len(s["record_candidates"]) for s in sites)
    return {
        "format": "field-brain-migration-preview-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"path": str(path), "sha256": hashlib.sha256(raw_bytes).hexdigest(), "bytes": len(raw_bytes)},
        "safety": {
            "source_executed": False,
            "database_written": False,
            "all_candidates_require_review": True,
        },
        "counts": {"sites": len(sites), "candidates": total_candidates},
        "sites": sites,
    }


def write_preview(source_path: str | Path, output_path: str | Path) -> Path:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = preview_codex_static_mvp(source_path)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
