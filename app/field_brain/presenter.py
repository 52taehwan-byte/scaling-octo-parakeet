"""Safe, Korean-language presenter dictionaries for Jinja templates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Iterable, Mapping

from .analytics import (
    aggregate_financial_breakdowns,
    current_money_rows,
    site_financial_breakdown,
)
from .originals import evidence_label
from .repository import FieldBrainRepository
from .workflow import build_workflow_focus
from .briefing import recent_records, note_sections
from .attendance import labor_forecast


SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")

SITE_LABELS = {
    "lead": "문의 받음",
    "estimating": "견적 확인 중",
    "scheduled": "작업 예정",
    "in_progress": "작업 중",
    "paused": "보류",
    "completed": "작업 완료",
    "settled": "정산 완료",
    "cancelled": "취소",
    "inquiry": "문의 받음",
    "survey": "현장 확인",
    "quoted": "견적 제출",
}
ROLE_LABELS = {
    "client": "고객",
    "buyer": "발주자",
    "site_manager": "현장 담당자",
    "owner": "사업주",
    "employee": "직원",
    "skilled_worker": "기공·작업자",
    "helper": "조공",
    "subcontractor": "외주 작업자",
    "equipment_operator": "장비 기사",
    "waste_vendor": "폐기물 업체",
    "other": "기타",
}
PHASE_LABELS = {
    "pre_estimate": "현장 확인·견적",
    "contract": "계약",
    "preparation": "작업 준비",
    "demolition": "현장 작업",
    "waste": "폐기물 처리",
    "restoration": "복원",
    "settlement": "정산·잔금",
    "aftercare": "사후 확인",
    "other": "기타",
    "survey": "현장 확인",
    "quote": "견적",
    "execution": "현장 작업",
    "follow_up": "사후 확인",
}
PURPOSE_LABELS = {
    "base_quote": "기본 견적",
    "addon_quote": "추가 견적",
    "discount": "할인·절삭",
    "commission": "중개 수수료",
    "labor_cost": "인건비",
    "equipment_cost": "장비비",
    "waste_cost": "폐기물 처리비",
    "material_cost": "자재비",
    "transport_cost": "운반비",
    "outsource_cost": "외주비",
    "scrap_income": "고물 판매 수입",
    "deposit": "계약금·중도금 입금",
    "receivable": "미수금 메모",
    "settlement": "잔금 입금",
    "refund": "환불",
    "tax": "세금",
    "other": "기타 금액",
}
PROGRESSION_LABELS = {
    "candidate": "검토 후보",
    "offered": "제안함",
    "agreed": "합의함",
    "claimed": "청구함",
    "confirmed": "입출금 확인",
    "settled": "정산 완료",
    "void": "무효",
}
ACTUALNESS_LABELS = {"estimated": "예상", "actual": "실제"}
RISK_LABELS = {
    "safety": "안전",
    "scope": "작업 범위",
    "customer": "고객",
    "schedule": "일정",
    "cost": "비용",
    "legal": "법률·허가",
    "quality": "품질",
    "other": "기타",
    "payment": "입금·미수금",
    "waste": "폐기물",
}
EVENT_LABELS = {
    "call": "통화",
    "message": "문자·메신저",
    "visit": "현장 방문",
    "quote": "견적",
    "decision": "결정",
    "work": "작업",
    "payment": "입금·정산",
    "note": "메모",
    "inquiry": "첫 문의",
}
TARGET_LABELS = {
    "event": "타임라인",
    "task": "할 일",
    "money_item": "돈 기록",
    "risk": "위험",
    "schedule_item": "일정 후보",
}
ACTION_LABELS = {
    "create": "새 기록",
    "review": "검토 결과",
    "supersede": "정정 확정",
    "propose_correction": "정정 제안",
    "update_cost_completeness": "비용 범위 변경",
}


def _industry_data(site: Mapping[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(site.get("industry_data_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            return None
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=SEOUL)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SEOUL)
    return parsed.astimezone(SEOUL)


def date_label(value: Any, *, include_time: bool = False) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y.%m.%d %H:%M" if include_time else "%Y.%m.%d")


def schedule_day_label(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    weekday = ("월", "화", "수", "목", "금", "토", "일")[parsed.weekday()]
    return f"{parsed.month}월 {parsed.day}일 ({weekday})"


def present_schedule_rows(rows: Iterable[Mapping[str, Any]], sites: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    site_names = {str(site["id"]): str(site["name"]) for site in sites}
    result = []
    for source in rows:
        row = dict(source)
        kind = str(row.get("schedule_type"))
        precision = str(row.get("time_precision") or "exact")
        include_time = precision in {"exact", "approximate"}
        result.append(
            {
                **row,
                "type_label": "철거 작업" if kind == "work" else "견적 방문",
                "type_class": "work" if kind == "work" else "visit",
                "status_label": {"scheduled": "예정", "completed": "완료", "cancelled": "취소됨"}.get(
                    str(row.get("business_status")), "상태 미확인"
                ),
                "start_label": date_label(row.get("start_at"), include_time=include_time),
                "date_key": _parse_datetime(row.get("start_at")).date().isoformat() if _parse_datetime(row.get("start_at")) else "",
                "day_label": schedule_day_label(row.get("start_at")),
                "time_label": ((_parse_datetime(row.get("start_at")).strftime("%H:%M")
                                if _parse_datetime(row.get("start_at")) else None)
                               if include_time else "시간 미정"),
                "end_label": date_label(row.get("end_at"), include_time=include_time),
                "end_time_label": (_parse_datetime(row.get("end_at")).strftime("%H:%M")
                                   if _parse_datetime(row.get("end_at")) else None),
                "site_name": site_names.get(str(row.get("site_id"))) if row.get("site_id") else None,
                "detail_url": f"/schedules/{row['id']}",
                "is_past": bool(
                    _parse_datetime(row.get("start_at"))
                    and _parse_datetime(row.get("start_at")).date() < datetime.now(SEOUL).date()
                ),
            }
        )
    return result


def _is_overdue(value: Any, status: str) -> bool:
    parsed = _parse_datetime(value)
    return bool(parsed and parsed.date() < date.today() and status not in {"done", "cancelled"})


def _event_heads(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = [dict(row) for row in rows]
    replaced = {row.get("supersedes_event_id") for row in copied if row.get("supersedes_event_id")}
    return [
        row
        for row in copied
        if row["id"] not in replaced
        and row.get("legacy_status") not in {"superseded", "reference_only", "excluded"}
        and row.get("review_status") != "rejected"
    ]


def _brief_excerpt(value: Any, *, limit: int = 120) -> str | None:
    """Pick one useful, display-safe line from a longer field note."""
    if not isinstance(value, str):
        return None
    ignored_labels = {
        "오늘의 철거 기록", "날짜", "현장", "오늘 한 일", "함께 일한 사람",
        "차량·장비", "폐기물·고물", "돈", "사장이 한 말", "예상과 달랐던 일",
        "남은 일과 일정", "궁금하거나 이상했던 점",
    }
    for raw_line in value.splitlines():
        line = " ".join(raw_line.strip().lstrip("-*■# ").split())
        if not line:
            continue
        label = line.rstrip(":：").strip("[] ")
        if label in ignored_labels or label.startswith("날짜:") or label.startswith("현장:"):
            continue
        if len(line) < 6:
            continue
        return line if len(line) <= limit else f"{line[:limit - 1].rstrip()}…"
    return None


def _build_site_briefing(
    site: Mapping[str, Any],
    *,
    timeline: list[Mapping[str, Any]],
    active_tasks: list[Mapping[str, Any]],
    open_risks: list[Mapping[str, Any]],
    next_schedule: Mapping[str, Any] | None,
) -> dict[str, Any]:
    explicit_scope = _brief_excerpt(site.get("scope_summary"), limit=160)
    if explicit_scope:
        headline, basis, confidence = explicit_scope, "등록된 작업 범위 기준", "확인된 범위"
    else:
        latest = timeline[0] if timeline else None
        headline = _brief_excerpt(latest.get("summary") if latest else None, limit=160)
        if headline:
            basis, confidence = "최근 작업일지에서 자동 요약", "기록 기반 요약"
        elif active_tasks:
            headline = f"우선 처리할 일: {active_tasks[0]['title']}"
            basis, confidence = "남은 일에서 자동 요약", "할 일 기반 요약"
        elif next_schedule:
            headline = str(next_schedule.get("summary") or next_schedule.get("title") or "예정된 일정이 있습니다.")
            basis, confidence = "등록된 일정에서 자동 요약", "일정 기반 요약"
        else:
            headline = "아직 이 현장을 설명할 업무 기록이 없습니다."
            basis, confidence = "작업일지나 업무 자료를 넣으면 자동으로 요약합니다.", "자료 필요"
    missing = []
    if not site.get("scope_summary"):
        missing.append("작업 범위")
    if not next_schedule:
        missing.append("다음 일정")
    if not active_tasks:
        missing.append("남은 일")
    return {"headline": headline, "basis": basis, "confidence_label": confidence, "missing_labels": missing}


def _pending_by_site(pending: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in pending:
        site_id = item.get("site_id")
        if site_id:
            result[str(site_id)] = result.get(str(site_id), 0) + 1
    return result


def present_money_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    heads, effective = current_money_rows(rows)
    effective_by_head = {str(row["chain_head_id"]): row for row in effective}
    display_rows: list[dict[str, Any]] = []
    for head in heads:
        if head.get("legacy_status") in {"superseded", "reference_only", "excluded"}:
            continue
        row = effective_by_head.get(str(head["id"]), dict(head))
        if row.get("review_status") == "rejected":
            continue
        note = row.get("notes") or row.get("description") or row.get("title")
        if row.get("has_pending_correction"):
            note = f"{note} · 새 정정안 검토 중"
        direction = "out" if row.get("direction") == "outflow" else "in" if row.get("direction") == "inflow" else "neutral"
        display_rows.append(
            {
                "id": row["id"],
                "purpose_label": PURPOSE_LABELS.get(str(row.get("purpose")), "돈 기록"),
                "purpose": row.get("purpose"),
                "direction": direction,
                "direction_label": {"out": "지출", "in": "수입", "neutral": "조정"}[direction],
                "amount": row.get("amount_krw"),
                "note": note,
                "actualness_label": ACTUALNESS_LABELS.get(str(row.get("actualness"))),
                "progression_label": PROGRESSION_LABELS.get(str(row.get("progression"))),
                "occurred_on_label": date_label(row.get("occurred_at")),
                "epistemic_status": row.get("epistemic_type"),
                "review_status": row.get("review_status"),
            }
        )
    return display_rows


def build_site_context(
    repo: FieldBrainRepository,
    workspace_id: str,
    site_row: Mapping[str, Any],
    *,
    pending: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    site = dict(site_row)
    if site.get("workspace_id") != workspace_id:
        raise LookupError("현재 작업공간의 현장이 아닙니다.")
    pending_rows = pending if pending is not None else repo.pending_reviews(workspace_id)
    site_pending = [item for item in pending_rows if item.get("site_id") == site["id"]]

    roles = repo.list_for_site(site["id"], "role")
    people = {row["id"]: row for row in repo.list_people(workspace_id)}
    tasks_raw = repo.list_for_site(site["id"], "task")
    money_raw = repo.list_for_site(site["id"], "money_item")
    risks_raw = repo.list_for_site(site["id"], "risk")
    events_raw = repo.list_for_site(site["id"], "event")

    tasks = []
    for task in tasks_raw:
        if task.get("review_status") == "rejected" or task.get("legacy_status") in {"reference_only", "excluded"}:
            continue
        assignee = people.get(task.get("assignee_person_id"))
        pack_phase = task.get("industry_phase") or task.get("phase")
        tasks.append(
            {
                "id": task["id"],
                "title": task["title"],
                "phase_label": PHASE_LABELS.get(str(pack_phase), "기타"),
                "assignee_name": assignee.get("display_name") if assignee else None,
                "due_date_label": date_label(task.get("due_at")),
                "due_at": task.get("due_at"),
                "status": task.get("business_status"),
                "is_overdue": _is_overdue(task.get("due_at"), str(task.get("business_status"))),
                "review_status": task.get("review_status"),
            }
        )
    tasks.sort(key=lambda row: (row["status"] in {"done", "cancelled"}, row.get("due_at") or "9999", row["title"]))

    money_by_id = {row["id"]: row for row in money_raw}
    risks = []
    for risk in risks_raw:
        if risk.get("review_status") == "rejected" or risk.get("legacy_status") in {"reference_only", "excluded"}:
            continue
        linked = money_by_id.get(risk.get("estimated_extra_cost_money_item_id"))
        category = risk.get("industry_category") or risk.get("category")
        risks.append(
            {
                "id": risk["id"],
                "title": risk["title"],
                "category_label": RISK_LABELS.get(str(category), "기타"),
                "severity": risk.get("severity"),
                "response": risk.get("response_plan"),
                "estimated_additional_cost": linked.get("amount_krw") if linked else None,
                "is_resolved": risk.get("business_status") in {"mitigated", "closed"},
                "resolution_evidence": risk.get("resolution_evidence_ref"),
                "review_status": risk.get("review_status"),
            }
        )

    timeline = []
    for event in sorted(
        _event_heads(events_raw),
        key=lambda row: (row.get("occurred_at") or row.get("created_at") or "", row["id"]),
        reverse=True,
    ):
        timeline.append(
            {
                "id": event["id"],
                "title": event["title"],
                "summary": event.get("description"),
                "kind_label": EVENT_LABELS.get(str(event.get("event_type")), "업무 기록"),
                "occurred_at_label": date_label(event.get("occurred_at") or event.get("created_at"), include_time=True),
                "occurred_at_iso": event.get("occurred_at") or event.get("created_at"),
                "source_label": evidence_label(event.get("evidence_ref")),
                "epistemic_status": event.get("epistemic_type"),
                "review_status": event.get("review_status"),
            }
        )

    role_rows = []
    for role in roles:
        person = people.get(role.get("person_id"), {})
        role_rows.append(
            {
                "person_id": role.get("person_id"),
                "person_name": person.get("display_name") or "이름 미확인",
                "role_name": role.get("role_type"),
                "role_label": role.get("role_label") or ROLE_LABELS.get(str(role.get("role_type"))),
                "contact_note": person.get("notes"),
                "review_status": role.get("review_status") or "approved",
            }
        )

    active_tasks = [row for row in tasks if row["status"] not in {"done", "cancelled"}]
    open_risks = [row for row in risks if not row["is_resolved"]]
    industry = _industry_data(site)
    stage_key = str(industry.get("ui_stage") or site.get("business_status"))
    now = datetime.now(SEOUL)
    site_schedules = [
        row for row in repo.list_schedule_items(workspace_id, review_status="approved")
        if row.get("site_id") == site["id"]
        and row.get("business_status") != "cancelled"
        and (_parse_datetime(row.get("start_at")) or now) >= now
    ]
    site_schedules.sort(key=lambda row: _parse_datetime(row.get("start_at")) or datetime.max.replace(tzinfo=SEOUL))
    next_schedule_row = site_schedules[0] if site_schedules else None
    next_schedule_at = next_schedule_row.get("start_at") if next_schedule_row else (
        site.get("planned_start_at") or (active_tasks[0].get("due_at") if active_tasks else None)
    )
    briefing = _build_site_briefing(site, timeline=timeline, active_tasks=active_tasks,
                                    open_risks=open_risks, next_schedule=next_schedule_row)

    target_ids = {site["id"]}
    for collection in (roles, tasks_raw, money_raw, risks_raw, events_raw):
        target_ids.update(str(row["id"]) for row in collection)
    audit_rows = []
    for entry in repo.audit_log(workspace_id, limit=200):
        if str(entry.get("target_id")) not in target_ids:
            continue
        audit_rows.append(
            {
                "created_at_label": date_label(entry.get("occurred_at"), include_time=True),
                "created_at_iso": entry.get("occurred_at"),
                "action_label": ACTION_LABELS.get(str(entry.get("action_type")), "기록 변경"),
                "summary": f"{TARGET_LABELS.get(entry.get('target_type'), '업무 기록')}의 변경 이력을 보존했습니다.",
            }
        )

    financial = site_financial_breakdown(
        money_raw, cost_completeness=str(site.get("cost_completeness") or "unknown")
    )
    workflow_focus = build_workflow_focus(
        site,
        tasks=tasks,
        risks=risks,
        financial=financial,
        pending_review_count=len(site_pending),
    )

    return {
        "site": {
            "id": site["id"],
            "name": site["name"],
            "address_display": site.get("address_text"),
            "stage_label": SITE_LABELS.get(stage_key, "단계 미정"),
            "schedule_label": date_label(site.get("planned_start_at")),
            "brief": briefing["headline"],
            "brief_basis": briefing["basis"],
            "brief_confidence_label": briefing["confidence_label"],
            "brief_missing_labels": briefing["missing_labels"],
            "next_task": active_tasks[0]["title"] if active_tasks else None,
            "next_schedule_label": date_label(next_schedule_at, include_time=bool(next_schedule_row)),
            "next_schedule_title": (
                next_schedule_row.get("summary") or next_schedule_row.get("title") if next_schedule_row else None
            ),
            "open_risk_count": len(open_risks),
            "review_count": len(site_pending),
            "cost_completeness": site.get("cost_completeness"),
        },
        "recent_records": recent_records(timeline)[:3],
        "attendance": labor_forecast(timeline[0].get("summary") if timeline else None,
                                     str(timeline[0].get('occurred_at_iso') or '')[:10] if timeline else '',
                                     repo.list_worker_rates(workspace_id)),
        "note_sections": note_sections(timeline[0].get("summary") if timeline else None),
        "financial": financial,
        "workflow_focus": workflow_focus,
        "site_roles": role_rows,
        "tasks": tasks,
        "money_items": present_money_rows(money_raw),
        "risks": risks,
        "timeline": timeline,
        "audit_events": audit_rows[:10],
        "_raw_money": money_raw,
    }


def present_review_items(
    pending: Iterable[Mapping[str, Any]],
    sites: Iterable[Mapping[str, Any]],
    evidence_rows: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    site_names = {row["id"]: row["name"] for row in sites}
    evidence_by_id = {str(row["id"]): row for row in (evidence_rows or [])}
    result = []
    for item in pending:
        target_type = str(item.get("target_type"))
        site_id = item.get("site_id")
        proposed: str | None = None
        if target_type == "money_item":
            amount = item.get("amount_krw")
            proposed = "금액 미확인" if amount is None else f"{int(amount):,}원"
        elif target_type == "event":
            proposed = item.get("description")
        elif target_type == "task":
            proposed = item.get("description") or item.get("title")
        elif target_type == "risk":
            proposed = item.get("response_plan") or item.get("description")
        elif target_type == "schedule_item":
            kind = "철거 작업" if item.get("schedule_type") == "work" else "견적 방문"
            when = date_label(item.get("start_at"), include_time=item.get("time_precision") in {"exact", "approximate"}) or "날짜 미확인"
            proposed = f"{kind} · {when} · {item.get('summary') or ''}"
        start_value = _parse_datetime(item.get("start_at")) if target_type == "schedule_item" else None
        end_value = _parse_datetime(item.get("end_at")) if target_type == "schedule_item" else None
        evidence_ref = str(item.get("evidence_ref") or "")
        evidence = evidence_by_id.get(evidence_ref.removeprefix("evidence:")) if evidence_ref.startswith("evidence:") else None
        candidate_excerpt = None
        if target_type == "money_item":
            candidate_excerpt = item.get("notes") or item.get("description") or item.get("title")
        elif target_type in {"task", "risk", "event"}:
            candidate_excerpt = item.get("description") or item.get("title")
        if isinstance(candidate_excerpt, str):
            candidate_excerpt = candidate_excerpt.removeprefix("오늘의 철거 기록에서 정리한 후보: ").strip()
        editable_note = item.get("notes") or item.get("description") or item.get("title") or ""
        if isinstance(editable_note, str):
            editable_note = editable_note.removeprefix("오늘의 철거 기록에서 정리한 후보: ").strip()
        result.append(
            {
                "id": item["id"],
                "target_type": target_type,
                "title": item.get("title") or "제목 미확인",
                "review_status": item.get("review_status"),
                "epistemic_status": item.get("epistemic_type"),
                "site_id": site_id,
                "site_name": site_names.get(site_id),
                "type_label": TARGET_LABELS.get(target_type, "업무 기록"),
                "created_at_label": date_label(item.get("created_at"), include_time=True),
                "reason": "원문 또는 근거와 맞는지 확인해 주세요.",
                "proposed_value": proposed,
                "source_excerpt": candidate_excerpt or (evidence.get("excerpt") if evidence else None),
                "source_url": (f"/schedules/{item['id']}" if target_type == "schedule_item"
                               else f"/sites/{site_id}#timeline" if site_id else "/review"),
                "url": f"/review#{item['id']}",
                "is_schedule": target_type == "schedule_item",
                "schedule_type": item.get("schedule_type"),
                "summary": item.get("summary") or "",
                "amount_value": item.get("amount_krw") if target_type == "money_item" else None,
                "purpose": item.get("purpose") if target_type == "money_item" else None,
                "note": editable_note,
                "address_text": item.get("address_text") or "",
                "start_date_value": start_value.date().isoformat() if start_value else "",
                "start_time_value": (
                    start_value.strftime("%H:%M") if start_value and item.get("time_precision") in {"exact", "approximate"} else ""
                ),
                "end_date_value": end_value.date().isoformat() if end_value else "",
                "end_time_value": (
                    end_value.strftime("%H:%M") if end_value and item.get("time_precision") in {"exact", "approximate"} else ""
                ),
            }
        )
    return result


def build_dashboard_context(
    repo: FieldBrainRepository,
    workspace_id: str,
) -> dict[str, Any]:
    sites = repo.list_sites(workspace_id)
    all_schedules = repo.list_schedule_items(workspace_id)
    pending = repo.pending_reviews(workspace_id)
    pending_counts = _pending_by_site(pending)
    site_rows = []
    urgent_tasks = []
    open_risks = []
    breakdowns = []

    for site in sites:
        detail = build_site_context(repo, workspace_id, site, pending=pending)
        breakdowns.append(detail["financial"])
        candidate_only_site = (
            str(site.get("notes") or "").startswith("카카오톡 일정 후보에서 만든 현장")
            and not any(row.get("site_id") == site["id"] and row.get("review_status") == "approved" for row in all_schedules)
        )
        if not candidate_only_site:
            site_rows.append({
                "id": site["id"],
                "name": site["name"],
                "stage_label": detail["site"]["stage_label"],
                "address_display": site.get("address_text"),
                "next_task": detail["site"]["next_task"],
                "next_schedule_label": detail["site"]["next_schedule_label"],
                "agreed_customer_amount": detail["financial"]["agreed_customer_amount"],
                "review_count": pending_counts.get(site["id"], 0),
                "updated_at_label": date_label(site.get("updated_at"), include_time=True),
            })
        for task in detail["tasks"]:
            if task["status"] in {"done", "cancelled"}:
                continue
            urgent_tasks.append(
                {
                    "title": task["title"],
                    "site_name": site["name"],
                    "due_date_label": task["due_date_label"],
                    "due_at": task.get("due_at"),
                    "is_overdue": task["is_overdue"],
                }
            )
        for risk in detail["risks"]:
            if risk["is_resolved"]:
                continue
            open_risks.append(
                {
                    "site_id": site["id"],
                    "site_name": site["name"],
                    "title": risk["title"],
                    "severity": risk["severity"],
                    "response": risk["response"],
                }
            )

    urgent_tasks.sort(key=lambda row: (not row["is_overdue"], row.get("due_at") or "9999", row["title"]))
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    open_risks.sort(key=lambda row: (severity_rank.get(str(row["severity"]), 9), row["title"]))
    review_items = present_review_items(pending, sites, repo.list_evidence(workspace_id))

    insights = []
    if pending:
        insights.append(
            {
                "title": f"확정 전 기록 {len(pending)}건을 먼저 확인하세요",
                "body": "금액·일정·위험 후보를 확정해야 현장 판단에 안전하게 사용할 수 있습니다.",
                "action_label": "검토함 열기",
                "action_url": "/review",
            }
        )
    incomplete = [site for site in sites if site.get("cost_completeness") != "complete"]
    if incomplete:
        insights.append(
            {
                "title": "실행비 범위가 아직 덜 채워진 현장이 있습니다",
                "body": "인건비·장비·폐기물·운반·외주비가 모두 잡히기 전에는 마진을 확정하지 않습니다.",
                "action_label": "현장 확인",
                "action_url": f"/sites/{incomplete[0]['id']}#money",
            }
        )
    if any(risk.get("severity") in {"high", "critical"} for risk in open_risks):
        insights.append(
            {
                "title": "높은 위험은 작업 전에 대응을 확정하세요",
                "body": "범위 변경·안전·비용 위험은 일정과 하루 마진을 동시에 흔들 수 있습니다.",
            }
        )

    schedules = present_schedule_rows(
        [row for row in all_schedules if row.get("review_status") == "approved"], sites
    )
    active_schedules = [row for row in schedules if row.get("business_status") == "scheduled"]
    return {
        "financial_summary": aggregate_financial_breakdowns(breakdowns),
        "sites": site_rows,
        "urgent_tasks": urgent_tasks,
        "open_risks": open_risks,
        "review_items": review_items,
        "insights": insights,
        "schedule_items": active_schedules[:8],
    }
