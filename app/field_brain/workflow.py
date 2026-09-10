"""Deterministic next-action guidance for a Field Brain site.

This layer intentionally does not call an AI model.  It turns already reviewed
business records into a small, stable briefing that the UI and a future GPT
proposal can share.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


PHASES: dict[str, tuple[str, str]] = {
    "lead": ("문의", "작업 범위를 이해하려면 무엇을 먼저 확인해야 하나요?"),
    "estimating": ("견적", "손해 보지 않는 견적을 위해 무엇이 아직 부족한가요?"),
    "scheduled": ("작업 준비", "작업 시작 전에 빠진 준비는 없나요?"),
    "in_progress": ("작업", "오늘 현장을 안전하게 끝내려면 무엇을 먼저 해야 하나요?"),
    "paused": ("작업 보류", "다시 시작하려면 어떤 문제가 먼저 풀려야 하나요?"),
    "completed": ("완료·정산", "작업을 돈과 증거까지 빠짐없이 마무리했나요?"),
    "settled": ("회고", "다음 현장에서 더 잘하려면 무엇을 남겨야 하나요?"),
    "cancelled": ("종료", "취소 사유와 남은 책임을 모두 정리했나요?"),
}

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _action(title: str, reason: str, href: str, kind: str) -> dict[str, str]:
    return {"title": title, "reason": reason, "href": href, "kind": kind}


def build_workflow_focus(
    site: Mapping[str, Any],
    *,
    tasks: Sequence[Mapping[str, Any]],
    risks: Sequence[Mapping[str, Any]],
    financial: Mapping[str, Any],
    pending_review_count: int = 0,
) -> dict[str, Any]:
    """Return one primary action and a few secondary checks for a site.

    Safety and missed commitments outrank ordinary phase guidance.  Duplicate
    titles are removed so the user is never told the same thing twice.
    """

    phase_key = str(site.get("business_status") or "lead")
    phase_label, question = PHASES.get(phase_key, ("현장 진행", "이 현장의 다음 행동은 무엇인가요?"))
    candidates: list[dict[str, str]] = []

    open_risks = [risk for risk in risks if not risk.get("is_resolved")]
    open_risks.sort(key=lambda row: (SEVERITY_RANK.get(str(row.get("severity")), 9), str(row.get("title"))))
    urgent_risk = next((risk for risk in open_risks if risk.get("severity") in {"critical", "high"}), None)
    if urgent_risk:
        candidates.append(
            _action(
                f"위험 먼저 대응: {urgent_risk.get('title')}",
                str(urgent_risk.get("response") or "대응 방법을 정한 뒤 작업을 진행해야 합니다."),
                "#risks",
                "risk",
            )
        )

    overdue = next(
        (task for task in tasks if task.get("is_overdue") and task.get("status") not in {"done", "cancelled"}),
        None,
    )
    if overdue:
        candidates.append(
            _action(
                f"늦어진 일 확인: {overdue.get('title')}",
                "예정일이 지났습니다. 새 일정을 정하거나 완료 여부를 확인하세요.",
                "#tasks",
                "overdue_task",
            )
        )

    active_task = next((task for task in tasks if task.get("status") not in {"done", "cancelled"}), None)
    completeness = str(site.get("cost_completeness") or "unknown")

    if phase_key == "lead":
        if not site.get("scope_summary"):
            candidates.append(_action("고객이 원하는 철거 범위 기록", "범위가 있어야 방문·견적·추가 작업을 구분할 수 있습니다.", "#add-event", "scope"))
        candidates.append(_action("다음 연락 또는 현장 방문 정하기", "문의가 멈추지 않도록 다음 약속을 할 일로 남기세요.", "#add-task", "schedule"))
    elif phase_key == "estimating":
        if completeness != "complete":
            candidates.append(_action("실행비와 필요한 작업일 확인", "인건비·장비·폐기물·운반·외주비가 있어야 마진을 판단할 수 있습니다.", "#money", "cost"))
        if financial.get("agreed_customer_amount") is None:
            candidates.append(_action("고객 견적과 포함 범위 정리", "금액뿐 아니라 포함·제외 작업을 함께 남겨야 추가 작업을 구분할 수 있습니다.", "#add-money", "quote"))
    elif phase_key == "scheduled":
        if not site.get("planned_start_at"):
            candidates.append(_action("작업 시작일 확정", "고객·인력·장비 동선을 맞추려면 날짜가 먼저 필요합니다.", "#add-task", "schedule"))
        candidates.append(_action("인원·차량·장비·폐기물 동선 점검", "현장 시작 전에 실행 조건과 책임자를 한 번에 확인하세요.", "#tasks", "preparation"))
    elif phase_key in {"in_progress", "paused"}:
        if active_task:
            candidates.append(_action(str(active_task.get("title")), "현재 열려 있는 할 일 중 가장 먼저 처리할 항목입니다.", "#tasks", "task"))
        else:
            candidates.append(_action("오늘 작업과 남은 일 기록", "중단되거나 다음 날 이어질 때 바로 재개할 수 있도록 남겨두세요.", "#add-event", "work_log"))
    elif phase_key == "completed":
        if completeness != "complete":
            candidates.append(_action("실제 실행비 빠진 항목 확인", "실제비가 모두 들어와야 현장 마진을 확정할 수 있습니다.", "#money", "cost"))
        candidates.append(_action("완료 사진과 고객 확인 남기기", "완료 범위와 상태를 증거로 남겨 분쟁과 누락을 막으세요.", "#add-event", "completion"))
    elif phase_key == "settled":
        candidates.append(_action("배운 점과 다음 견적 기준 남기기", "이번 현장의 판단을 다음 현장에서 재사용할 수 있는 사업 지식으로 바꾸세요.", "#add-event", "retrospective"))
    elif phase_key == "cancelled":
        candidates.append(_action("취소 사유와 남은 비용·약속 확인", "취소 이후에도 남는 비용과 고객 약속을 분명히 정리하세요.", "#timeline", "closure"))

    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        unique.append(item)

    if not unique:
        unique.append(_action("다음 행동 하나 정하기", "현장이 멈추지 않도록 가장 작은 다음 행동을 남기세요.", "#add-task", "task"))

    return {
        "phase_key": phase_key,
        "phase_label": phase_label,
        "question": question,
        "primary_action": unique[0],
        "secondary_actions": unique[1:3],
        "has_urgent_issue": unique[0]["kind"] in {"risk", "overdue_task"},
    }
