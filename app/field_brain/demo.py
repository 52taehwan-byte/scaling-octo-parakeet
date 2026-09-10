"""Idempotent, entirely fictional data for safe product demonstrations."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import uuid

from .repository import FieldBrainRepository


def _lineage() -> str:
    return f"demo-{uuid.uuid4()}"


def _ensure_demo_schedules(repo: FieldBrainRepository, workspace_id: str) -> None:
    if repo.list_schedule_items(workspace_id):
        return
    sites = repo.list_sites(workspace_id)
    if not sites:
        return
    work_site = next((site for site in sites if site.get("business_status") == "scheduled"), sites[0])
    visit_site = next((site for site in sites if site["id"] != work_site["id"]), work_site)
    day = date.today() + timedelta(days=1)
    repo.create_schedule_item(
        workspace_id, "work", work_site["name"], f"{day.isoformat()}T08:00+09:00",
        "가벽·바닥 철거와 폐기물 반출 (가상 일정)", site_id=work_site["id"],
        end_at=f"{day.isoformat()}T17:00+09:00", participants_text="가상 사업주, 가상 작업자",
        actor_type="system",
    )
    repo.create_schedule_item(
        workspace_id, "estimate_visit", "가상 창고 고객", f"{day.isoformat()}T10:00+09:00",
        "철거 범위와 폐기물 반출 동선 확인 (가상 일정)",
        customer_name="가상 창고 고객", address_text=visit_site.get("address_text") or "가상 주소",
        actor_type="system",
    )


def ensure_demo_data(repo: FieldBrainRepository) -> str:
    existing = repo.list_workspaces()
    if existing:
        workspace_id = str(existing[0]["id"])
        _ensure_demo_schedules(repo, workspace_id)
        return workspace_id

    workspace = repo.create_workspace(
        "Field Brain 가상 체험",
        settings={"fictional_demo": True},
        actor_type="system",
    )
    workspace_id = str(workspace["id"])

    customer = repo.create_person(
        workspace_id,
        "가상 고객",
        organization_name="새봄 인테리어 (가상)",
        notes="가상 데모 전용 인물이며 실제 연락처가 없습니다.",
        actor_type="system",
    )
    owner = repo.create_person(
        workspace_id,
        "가상 사업주",
        notes="가상 데모 전용 인물입니다.",
        actor_type="system",
    )
    worker = repo.create_person(
        workspace_id,
        "가상 작업자",
        notes="가상 데모 전용 인물입니다.",
        actor_type="system",
    )

    tomorrow = date.today() + timedelta(days=1)
    in_three_days = date.today() + timedelta(days=3)
    now = datetime.now(timezone.utc)

    active = repo.create_site(
        workspace_id,
        "새봄 상가 원상복구 (가상)",
        address_text="경기도 가상시 새봄로 (가상 주소)",
        business_status="scheduled",
        cost_completeness="complete",
        client_person_id=customer["id"],
        scope_summary="가벽·바닥·간판 철거와 입구 복원 범위를 확인하는 가상 현장",
        planned_start_at=tomorrow.isoformat(),
        actor_type="system",
    )
    repo.create_site_role(
        active["id"], customer["id"], "client", role_label="고객", is_primary_contact=True,
        actor_type="system",
    )
    repo.create_site_role(
        active["id"], owner["id"], "owner", role_label="사업주", actor_type="system",
    )
    repo.create_site_role(
        active["id"], worker["id"], "skilled_worker", role_label="작업자", actor_type="system",
    )

    repo.create_event(
        active["id"],
        "call",
        "고객 통화에서 철거 범위 후보 추출",
        description="가벽과 바닥 철거는 확인됐고, 간판 시트 제거 범위는 사람이 다시 확인해야 합니다.",
        occurred_at=now.isoformat(),
        time_precision="exact",
        epistemic_type="claim",
        review_status="pending",
        evidence_ref="demo://fictional-call-transcript",
        actor_type="ai",
    )
    repo.create_event(
        active["id"],
        "visit",
        "가상 현장 방문과 사진 촬영",
        description="입구, 바닥, 가벽, 외부 간판을 가상 예시로 촬영했습니다.",
        occurred_at=now.isoformat(),
        time_precision="exact",
        epistemic_type="fact",
        review_status="approved",
        evidence_ref="demo://fictional-photo-set",
        actor_type="system",
    )

    money = [
        ("기본 철거·복원 합의금", 4_800_000, "base_quote", "agreed", "estimated", "inflow"),
        ("중개 수수료", 480_000, "commission", "confirmed", "actual", "outflow"),
        ("작업 인건비", 800_000, "labor_cost", "agreed", "estimated", "outflow"),
        ("입구 복원 외주", 1_400_000, "outsource_cost", "agreed", "estimated", "outflow"),
        ("폐기물 반출·처리", 600_000, "waste_cost", "confirmed", "actual", "outflow"),
        ("장비 예상비", 220_000, "equipment_cost", "agreed", "estimated", "outflow"),
        ("계약금 입금", 1_500_000, "deposit", "confirmed", "actual", "inflow"),
    ]
    for title, amount, purpose, progression, actualness, direction in money:
        repo.create_money_item(
            active["id"], _lineage(), title, amount, purpose, progression, actualness, direction,
            epistemic_type="fact", review_status="approved", evidence_ref="demo://fictional-ledger",
            actor_type="system",
        )

    repo.create_task(
        active["id"],
        "간판 시트 제거 범위를 고객에게 문자로 확정",
        phase="pre_estimate",
        assignee_person_id=owner["id"],
        due_at=tomorrow.isoformat(),
        priority="high",
        epistemic_type="recommendation",
        review_status="pending",
        evidence_ref="demo://fictional-call-transcript",
        actor_type="ai",
    )
    repo.create_task(
        active["id"],
        "작업 전 안전화와 보호구 확인",
        phase="preparation",
        assignee_person_id=owner["id"],
        due_at=tomorrow.isoformat(),
        priority="normal",
        epistemic_type="decision",
        review_status="approved",
        evidence_ref="demo://fictional-safety-rule",
        actor_type="system",
    )
    repo.create_risk(
        active["id"],
        "scope",
        "시트 제거 범위가 확정되지 않음",
        description="철거 당일 범위가 늘면 일정과 비용이 함께 바뀔 수 있습니다.",
        severity="high",
        response_plan="작업 전에 고객 문자로 존치·철거 범위를 확정합니다.",
        epistemic_type="inference",
        review_status="pending",
        evidence_ref="demo://fictional-call-transcript",
        actor_type="ai",
    )

    lead = repo.create_site(
        workspace_id,
        "푸른마을 창고 철거 문의 (가상)",
        address_text="인천광역시 가상구 푸른길 (가상 주소)",
        business_status="estimating",
        cost_completeness="partial",
        scope_summary="사진만 전달받아 현장 확인이 필요한 가상 문의",
        planned_start_at=in_three_days.isoformat(),
        actor_type="system",
    )
    repo.create_money_item(
        lead["id"],
        _lineage(),
        "폐기물 처리비 금액 미확인",
        None,
        "waste_cost",
        "candidate",
        "estimated",
        "outflow",
        epistemic_type="claim",
        review_status="pending",
        evidence_ref="demo://fictional-message-export",
        actor_type="ai",
    )
    repo.create_task(
        lead["id"],
        "현장 방문 후 폐기물 종류와 반출 동선 확인",
        phase="pre_estimate",
        due_at=in_three_days.isoformat(),
        priority="high",
        epistemic_type="recommendation",
        review_status="pending",
        evidence_ref="demo://fictional-message-export",
        actor_type="ai",
    )

    _ensure_demo_schedules(repo, workspace_id)
    return workspace_id
