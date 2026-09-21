"""Local-only FastAPI application for the first Field Brain working slice."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import calendar
import hmac
import re
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from .backup import create_backup
from .capture import extract_candidates
from .direct_expenses import explicit_expenses
from .config import Settings, load_settings
from .demo import ensure_demo_data
from .originals import StoredOriginal, store_text_original
from .schedule_import import import_schedule_candidates
from .presenter import (
    build_dashboard_context,
    build_site_context,
    present_money_rows,
    present_schedule_rows,
    present_review_items,
    schedule_day_label,
)
from .repository import (
    ConflictError,
    FieldBrainError,
    FieldBrainRepository,
    NotFoundError,
    ValidationError,
)


APP_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = APP_ROOT / "templates"
STATIC_DIR = APP_ROOT / "static"
SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")

NOTICE_MESSAGES = {
    "site_created": "현장을 만들었습니다.",
    "person_created": "사람과 현장 역할을 저장했습니다.",
    "task_created": "할 일을 저장했습니다.",
    "money_created": "돈 기록을 저장했습니다.",
    "agreement_applied": "수주 소식을 처리했습니다. 같은 소식은 중복 반영하지 않습니다. 현재 수주금액과 작업 일정은 아래에서 볼 수 있습니다.",
    "risk_created": "위험요소를 저장했습니다.",
    "event_created": "타임라인 기록을 저장했습니다.",
    "capture_created": "원문을 보존하고 이 현장 기록에 정리했습니다. 틀린 내용만 고쳐 주세요.",
    "cost_scope_saved": "실행비 입력 범위와 변경 이유를 이력에 남겼습니다.",
    "review_saved": "검토 결과와 판단 근거를 이력에 남겼습니다.",
    "schedule_candidate_updated": "일정 후보를 고치고 수정 이유를 이력에 남겼습니다.",
    "daily_log_created": "오늘의 철거 기록과 원문을 현장에 저장했습니다.",
    "money_candidate_updated": "금액을 고쳐 확정하고 변경 이력을 남겼습니다.",
    "expense_created": "지출을 장부에 기록했습니다.",
    "estimate_result_saved": "견적 방문 결과와 금액 구성을 저장했습니다.",
    "estimate_standard_created": "현장 참고 내용을 저장했습니다.",
    "estimate_standard_archived": "현장 참고 내용을 보관했습니다.",
    "estimate_standard_restored": "현장 참고 내용을 다시 사용합니다.",
    "backup_created": "로컬 원장 백업을 만들고 무결성을 확인했습니다.",
    "work_schedule_created": "철거 작업 일정을 저장했습니다.",
    "estimate_visit_created": "견적 방문 일정을 저장했습니다.",
    "schedule_sources_imported": "자료를 보존하고 확인할 수 있는 일정을 종류별로 정리했습니다.",
    "schedule_rescheduled": "새 일정으로 바꾸고 이전 날짜와 변경 이유를 이력에 남겼습니다.",
    "schedule_cancelled": "일정을 삭제하지 않고 취소 상태와 이유를 남겼습니다.",
    "schedule_restored": "취소했던 일정을 다시 예정으로 돌렸습니다.",
}

SITE_STAGE_MAP = {
    "inquiry": "lead",
    "survey": "estimating",
    "quoted": "estimating",
    "scheduled": "scheduled",
    "in_progress": "in_progress",
    "completed": "completed",
    "paused": "paused",
}
ROLE_MAP = {
    "customer": ("client", "고객"),
    "owner": ("owner", "사업주"),
    "worker": ("skilled_worker", "직원·작업자"),
    "subcontractor": ("subcontractor", "외주 작업자"),
    "broker": ("other", "중개자"),
    "other": ("other", "기타"),
}
TASK_PHASE_MAP = {
    "survey": "pre_estimate",
    "quote": "pre_estimate",
    "preparation": "preparation",
    "execution": "demolition",
    "waste": "waste",
    "settlement": "settlement",
    "follow_up": "aftercare",
}
TASK_STATUS_MAP = {
    "open": "open",
    "in_progress": "in_progress",
    "waiting": "blocked",
    "done": "done",
}
RISK_CATEGORY_MAP = {
    "scope": "scope",
    "safety": "safety",
    "schedule": "schedule",
    "cost": "cost",
    "payment": "cost",
    "waste": "other",
    "other": "other",
}
MONEY_PURPOSES = {
    "base_quote",
    "addon_quote",
    "discount",
    "commission",
    "labor_cost",
    "equipment_cost",
    "waste_cost",
    "material_cost",
    "transport_cost",
    "outsource_cost",
    "scrap_income",
    "deposit",
    "settlement",
    "refund",
    "tax",
    "other",
}
INFLOW_PURPOSES = {"base_quote", "addon_quote", "scrap_income", "deposit", "settlement"}
OUTFLOW_PURPOSES = {
    "commission",
    "labor_cost",
    "equipment_cost",
    "waste_cost",
    "material_cost",
    "transport_cost",
    "outsource_cost",
    "refund",
    "tax",
}


def _text(value: Any, *, maximum: int, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError("required")
    if len(result) > maximum:
        raise ValueError("too_long")
    return result


def _date(value: str) -> str | None:
    clean = value.strip()
    if not clean:
        return None
    try:
        return datetime.fromisoformat(clean).date().isoformat()
    except ValueError as exc:
        raise ValueError("invalid_date") from exc


def _local_datetime(value: str) -> str | None:
    clean = value.strip()
    if not clean:
        return None
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("invalid_datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SEOUL)
    return parsed.isoformat(timespec="minutes")


def _schedule_form_datetime(date_value: str, time_value: str = "") -> str:
    clean_date = _date(date_value)
    if clean_date is None:
        raise ValueError("invalid_date")
    clean_time = time_value.strip()
    if clean_time:
        try:
            parsed_time = datetime.strptime(clean_time, "%H:%M").time()
        except ValueError as exc:
            raise ValueError("invalid_time") from exc
    else:
        parsed_time = datetime.min.time()
    return datetime.combine(datetime.fromisoformat(clean_date).date(), parsed_time, SEOUL).isoformat(timespec="minutes")


def _optional_amount(value: str) -> int | None:
    clean = value.strip().replace(",", "")
    if not clean:
        return None
    if not clean.isdecimal():
        raise ValueError("invalid_amount")
    amount = int(clean)
    if amount > 100_000_000_000:
        raise ValueError("too_large")
    return amount


def _safe_notice(request: Request) -> str | None:
    return NOTICE_MESSAGES.get(request.query_params.get("notice", ""))


async def _form_dict(request: Request) -> dict[str, str]:
    form = await request.form()
    return {str(key): str(value) for key, value in form.items()}


def _validate_csrf(request: Request, values: dict[str, str]) -> None:
    supplied = values.get("csrf_token", "")
    expected = str(request.app.state.csrf_token)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="요청 확인값이 만료됐습니다. 화면을 새로 열어 다시 저장해 주세요.")


def _store_optional_original(
    settings: Settings,
    text: str,
    *,
    kind: str,
) -> StoredOriginal | None:
    return store_text_original(settings.originals_dir, text, kind=kind) if text.strip() else None


def _cleanup_uncommitted(original: StoredOriginal | None) -> None:
    if original is not None:
        original.path.unlink(missing_ok=True)


def _owned_site(repo: FieldBrainRepository, workspace_id: str, site_id: str) -> dict[str, Any]:
    try:
        site = repo.get_site(site_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.") from exc
    if site.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.")
    return site


def _common_context(request: Request, *, active_page: str) -> dict[str, Any]:
    return {
        "request": request,
        "active_page": active_page,
        "notice": _safe_notice(request),
        "global_error": None,
        "csrf_token": request.app.state.csrf_token,
        "demo_mode": request.app.state.settings.demo,
    }


def _render(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
    )


def _site_detail_response(
    request: Request,
    site_id: str,
    *,
    form_name: str | None = None,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    global_error: str | None = None,
    status_code: int = 422,
) -> HTMLResponse:
    repo: FieldBrainRepository = request.app.state.repo
    workspace_id: str = request.app.state.workspace_id
    site = _owned_site(repo, workspace_id, site_id)
    context = _common_context(request, active_page="sites")
    detail = build_site_context(repo, workspace_id, site)
    detail.pop("_raw_money", None)
    context.update(detail)
    context.update(
        {
            "forms": {form_name: {"values": values or {}, "errors": errors or {}}}
            if form_name
            else {},
            "open_form": form_name or "",
            "global_error": global_error,
        }
    )
    return _render(request, "site_detail.html", context, status_code=status_code)


def _friendly_core_error(exc: FieldBrainError) -> str:
    if isinstance(exc, ConflictError):
        return "이미 새 버전이 있는 기록입니다. 화면을 새로 열어 현재 기록을 확인해 주세요."
    return "입력한 값의 조합을 저장할 수 없습니다. 표시된 항목과 상태를 다시 확인해 주세요."


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or load_settings()
    current_settings.ensure_directories()
    repo = FieldBrainRepository(current_settings.db_path)
    if current_settings.demo:
        workspace_id = ensure_demo_data(repo)
    else:
        workspaces = repo.list_workspaces()
        workspace_id = (
            str(workspaces[0]["id"])
            if workspaces
            else str(repo.create_workspace("내 철거 사업", actor_type="system")["id"])
        )
    if not repo.list_estimate_standards(workspace_id, include_archived=True):
        suffix = " (가상 기준)" if current_settings.demo else ""
        defaults = (
            ("process", "견적 계산 순서" + suffix,
             "1. 철거 범위를 확인한다.\n2. 실제 투입비와 소요 날짜를 계산한다.\n3. 하루 마진 기준을 더해 고객 견적을 낸다.",
             "고객 청구액과 실제 실행비를 섞지 않기 위해서다."),
            ("margin", "소규모 현장 하루 마진" + suffix,
             "규모가 작은 현장은 사장이 현장에 관여하는 하루당 순수 마진 50~100만원을 기준으로 본다.",
             "지금까지 관찰한 사장의 통상적인 견적 방식이다."),
            ("waste", "2.5톤 폐기물 고객 청구 기준" + suffix,
             "2.5톤 화물차 1차 반출·폐기는 통상 고객에게 70만원을 청구한다. 기름값과 실제 폐기 처리비가 포함된 고객 청구 기준이다.",
             "실제 처리장 지출액과 반드시 구분한다."),
            ("waste", "종합폐기물 실제 처리비 참고" + suffix,
             "종합폐기물은 2.5톤 차량 기준 실제 처리장에서 40만원이 들었던 사례가 있다. 과적 높이에 따라 추가금이 생길 수 있다.",
             "단가 확정값이 아니라 현장 사례이므로 견적 시 다시 확인한다."),
            ("process", "작업 시간도 견적한다" + suffix,
             "오늘 끝나는지, 사람이 더 필요한지, 며칠이 걸리는지를 먼저 판단한 뒤 인원과 장비를 계산한다.",
             "작업 지연과 갑작스러운 인원 추가를 줄이기 위해서다."),
        )
        for order, (category, title, rule, rationale) in enumerate(defaults, 1):
            repo.create_estimate_standard(workspace_id, category=category, title=title,
                                          rule_text=rule, rationale=rationale,
                                          source_note="철거 업무 대화에서 확정한 초기 기준", sort_order=order,
                                          actor_type="system")

    app = FastAPI(
        title="Field Brain",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = current_settings
    app.state.repo = repo
    app.state.workspace_id = workspace_id
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": str(exc.detail)})

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": "demo" if current_settings.demo else "local"}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        context = _common_context(request, active_page="dashboard")
        context.update(build_dashboard_context(repo, workspace_id))
        today = datetime.now(SEOUL)
        weekdays = ("월", "화", "수", "목", "금", "토", "일")
        context["today_label"] = f"{today.year}년 {today.month}월 {today.day}일 {weekdays[today.weekday()]}요일"
        context["today_schedule_items"] = [
            row for row in context.get("schedule_items", [])
            if row.get("date_key") == today.date().isoformat()
        ]
        return _render(request, "dashboard.html", context)

    @app.get("/schedules", response_class=HTMLResponse)
    async def schedules(request: Request, view: str = "work") -> HTMLResponse:
        selected = "visit" if view == "visit" else "work"
        sites = repo.list_sites(workspace_id)
        work_rows = present_schedule_rows(
            repo.list_schedule_items(workspace_id, schedule_type="work", review_status="approved"), sites
        )
        visit_rows = present_schedule_rows(
            repo.list_schedule_items(workspace_id, schedule_type="estimate_visit", review_status="approved"), sites
        )
        rows = work_rows if selected == "work" else visit_rows
        context = _common_context(request, active_page="schedules")
        context.update({
            "view": selected,
            "rows": [row for row in rows if not row.get("is_past")],
            "work_count": sum(not row.get("is_past") for row in work_rows),
            "visit_count": sum(not row.get("is_past") for row in visit_rows),
        })
        return _render(request, "schedules.html", context)

    @app.get("/more", response_class=HTMLResponse)
    async def more(request: Request) -> HTMLResponse:
        return _render(request, "more.html", _common_context(request, active_page="more"))

    @app.get("/records", response_class=HTMLResponse)
    async def records(request: Request) -> HTMLResponse:
        context = _common_context(request, active_page="records")
        context.update({
            "site_count": len(repo.list_sites(workspace_id)),
            "source_count": len(repo.list_sources(workspace_id)),
        })
        return _render(request, "records.html", context)

    @app.get("/business", response_class=HTMLResponse)
    async def business(request: Request) -> HTMLResponse:
        financial = build_dashboard_context(repo, workspace_id).get("financial_summary", {})
        standards = repo.list_estimate_standards(workspace_id)
        context = _common_context(request, active_page="business")
        context.update({
            "site_count": len(repo.list_sites(workspace_id)),
            "standard_count": len(standards),
            "quote_revenue": financial.get("agreed_customer_amount"),
            "estimated_cost": financial.get("estimated_cost"),
            "estimated_margin": financial.get("estimated_margin"),
            "recent_standards": standards[:3],
        })
        return _render(request, "business.html", context)

    @app.get("/ledger", response_class=HTMLResponse)
    async def ledger(request: Request, filter: str = "all") -> HTMLResponse:
        selected = filter if filter in {"all", "quote", "cost"} else "all"
        sites = repo.list_sites(workspace_id)
        rows: list[dict[str, Any]] = []
        for site in sites:
            for row in present_money_rows(repo.list_for_site(site["id"], "money_item")):
                row.update({"site_id": site["id"], "site_name": site["name"]})
                rows.append(row)
        confirmed = [row for row in rows if row.get("review_status") in {"approved", "corrected"}]
        quote_purposes = {"base_quote", "addon_quote", "discount"}
        business_rows = [
            row for row in rows
            if row.get("direction") == "out" or row.get("purpose") in quote_purposes
        ]
        quote_rows = [
            row for row in business_rows
            if row.get("purpose") in quote_purposes and row.get("review_status") in {"approved", "corrected"}
        ]
        cost_candidates = [row for row in business_rows if row.get("direction") == "out" and row.get("purpose") != "commission"]
        cost_rows = [
            row for row in cost_candidates if row.get("review_status") in {"approved", "corrected"}
            and row.get("actualness_label") == "실제"
        ]
        pending_money_count = sum(
            row.get("review_status") not in {"approved", "corrected"} for row in business_rows
        )
        for collection in (quote_rows, cost_rows):
            collection.sort(key=lambda row: (row.get("occurred_on_label") or "", row.get("site_name") or ""), reverse=True)
        financial = build_dashboard_context(repo, workspace_id).get("financial_summary", {})
        known_costs = [row for row in cost_rows if row.get("amount") is not None]
        actual_total = sum(row["amount"] for row in known_costs) if known_costs else None
        agreed_total = financial.get("agreed_customer_amount")
        current_balance = agreed_total - actual_total if agreed_total is not None and actual_total is not None else None

        def expense_bucket(row: dict[str, Any]) -> tuple[str, str]:
            purpose = str(row.get("purpose") or "")
            text = f"{row.get('note') or ''} {row.get('purpose_label') or ''}".lower()
            if purpose == "outsource_cost":
                return "outsource", "외주 작업"
            if purpose == "material_cost":
                return "materials", "자재·소모품"
            if purpose == "transport_cost":
                return "vehicle", "주유·통행·주차·운반"
            if any(word in text for word in ("포크레인", "굴삭기", "스카이차", "사다리차", "장비 기사", "장비작업자")):
                return "equipment", "장비·기사 포함"
            if purpose == "labor_cost":
                return "labor", "작업자"
            if purpose == "equipment_cost":
                return "equipment", "장비·기사 포함"
            if purpose == "waste_cost":
                if any(word in text for word in ("왈가닥", "콘크리트", "벽돌", "타일 폐기")):
                    return "waste", "왈가닥"
                if any(word in text for word in ("종합", "통합폐기물", "혼합폐기물")):
                    return "waste", "종합폐기물"
                if any(word in text for word in ("고물", "고철", "비철", "목재", "mdf", "합판")):
                    return "waste", "고물 처리"
                return "waste", "분류 확인 필요"
            if any(word in text for word in ("식비", "식사", "밥값", "점심", "저녁")):
                return "meals", "식비"
            if any(word in text for word in ("음료", "커피", "생수", "물값")):
                return "meals", "음료비"
            if any(word in text for word in ("주유", "기름값", "유류")):
                return "vehicle", "주유·통행·주차·운반"
            return "other", "기타 비용"

        group_specs = (
            ("labor", "직접 인건비", ("작업자",)),
            ("outsource", "외주비", ("외주 작업",)),
            ("equipment", "장비비", ("장비·기사 포함",)),
            ("waste", "폐기물 처리 비용", ("왈가닥", "종합폐기물", "고물 처리", "분류 확인 필요")),
            ("materials", "자재·소모품비", ("자재·소모품",)),
            ("vehicle", "차량·이동비", ("주유·통행·주차·운반",)),
            ("meals", "식비·음료비", ("식비", "음료비")),
            ("other", "기타 비용", ("기타 비용",)),
        )
        bucketed: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in cost_rows:
            group_key, item_key = expense_bucket(row)
            row["expense_group"] = group_key
            row["expense_item"] = item_key
            bucketed.setdefault((group_key, item_key), []).append(row)

        expense_groups = []
        for group_key, label, item_labels in group_specs:
            items = []
            for item_label in item_labels:
                item_rows = bucketed.get((group_key, item_label), [])
                known = [
                    int(row["amount"]) for row in item_rows
                    if row.get("amount") is not None
                ]
                items.append({"label": item_label, "rows": item_rows, "total": sum(known) if known else None})
            group_known = [
                int(row["amount"]) for row in known_costs if expense_bucket(row)[0] == group_key
            ]
            expense_groups.append({
                "key": group_key, "label": label, "items": items,
                "total": sum(group_known) if group_known else None,
                "count": sum(len(item["rows"]) for item in items),
            })
        context = _common_context(request, active_page="business")
        context.update({"filter": selected, "quote_rows": quote_rows, "expense_groups": expense_groups,
                        "pending_money_count": pending_money_count,
                        "quote_revenue": financial.get("agreed_customer_amount"),
                        "estimated_cost": actual_total,
                        "estimated_margin": current_balance,
                        "cost_groups": tuple((group["label"], group["total"]) for group in expense_groups)})
        return _render(request, "ledger.html", context)

    def expense_form_context(request: Request, values: dict[str, str] | None = None,
                             errors: dict[str, str] | None = None) -> dict[str, Any]:
        context = _common_context(request, active_page="business")
        context.update({
            "sites": repo.list_sites(workspace_id),
            "form_values": values or {"occurred_on": datetime.now(SEOUL).date().isoformat()},
            "form_errors": errors or {},
        })
        return context

    @app.get("/ledger/expense/new", response_class=HTMLResponse)
    async def ledger_expense_new(request: Request) -> HTMLResponse:
        return _render(request, "ledger_expense_new.html", expense_form_context(request))

    @app.get('/worker-rates', response_class=HTMLResponse)
    async def worker_rates(request: Request):
        context = _common_context(request, active_page='business')
        context.update(rates=repo.list_worker_rates(workspace_id), today=datetime.now(SEOUL).date().isoformat(), values={}, error=None)
        return _render(request, 'worker_rates.html', context)

    @app.post('/worker-rates', response_class=HTMLResponse)
    async def save_worker_rates(request: Request):
        values = await _form_dict(request)
        _validate_csrf(request, values)
        try:
            amount = _optional_amount(values.get('amount', ''))
            if amount is None:
                raise ValueError('일당을 입력해 주세요.')
            repo.save_worker_rate(workspace_id, values.get('name', ''), amount, values.get('effective_on', ''))
        except (ValueError, FieldBrainError):
            context = _common_context(request, active_page='business')
            context.update(rates=repo.list_worker_rates(workspace_id), today=datetime.now(SEOUL).date().isoformat(), values=values,
                           error='이름, 일당(원), 적용 시작일을 확인해 주세요.')
            return _render(request, 'worker_rates.html', context, status_code=422)
        return RedirectResponse('/worker-rates', status_code=303)

    @app.post("/ledger/expense/new", response_class=HTMLResponse)
    async def ledger_expense_create(request: Request) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        site_id = values.get("site_id", "").strip()
        try:
            _owned_site(repo, workspace_id, site_id)
        except (FieldBrainError, HTTPException):
            errors["site_id"] = "비용이 들어간 현장을 선택해 주세요."
        category = values.get("category", "")
        categories = {
            "worker": ("labor_cost", "작업자"), "equipment": ("equipment_cost", "장비·기사 포함"),
            "outsource": ("outsource_cost", "외주 작업"), "materials": ("material_cost", "자재·소모품"),
            "vehicle": ("transport_cost", "차량·이동비"),
            "debris": ("waste_cost", "왈가닥"), "mixed_waste": ("waste_cost", "종합폐기물"),
            "scrap_disposal": ("waste_cost", "고물 처리"), "food": ("other", "식비"),
            "drink": ("other", "음료비"), "fuel": ("transport_cost", "주유비"),
            "other": ("other", "기타 비용"),
        }
        if category not in categories:
            errors["category"] = "지출 종류를 선택해 주세요."
        try:
            amount = _optional_amount(values.get("amount", ""))
            if amount is None:
                raise ValueError("required")
        except ValueError:
            amount = None
            errors["amount"] = "실제로 지출한 금액을 원 단위 숫자로 적어 주세요."
        try:
            occurred_on = _date(values.get("occurred_on", ""))
            if occurred_on is None:
                raise ValueError("required")
        except ValueError:
            occurred_on = None
            errors["occurred_on"] = "지출한 날짜를 확인해 주세요."
        try:
            note = _text(values.get("note"), maximum=3_000, required=True)
        except ValueError:
            note = ""
            errors["note"] = "누구에게 무엇으로 쓴 돈인지 간단히 적어 주세요."
        if errors:
            return _render(request, "ledger_expense_new.html", expense_form_context(request, values, errors), status_code=422)
        purpose, category_label = categories[category]
        try:
            repo.create_money_item(
                site_id, f"manual-expense-{uuid4()}", f"{category_label} · {note}"[:300], amount,
                purpose, "settled", "actual", "outflow",
                notes=f"{category_label} · {note}", occurred_at=occurred_on,
                epistemic_type="fact", review_status="approved", actor_type="user",
            )
        except FieldBrainError as exc:
            context = expense_form_context(request, values, {})
            context["global_error"] = _friendly_core_error(exc)
            return _render(request, "ledger_expense_new.html", context, status_code=422)
        return RedirectResponse("/ledger?filter=cost&notice=expense_created", status_code=303)

    def daily_log_context(
        request: Request, *, values: dict[str, str] | None = None,
        errors: dict[str, str] | None = None, global_error: str | None = None,
    ) -> dict[str, Any]:
        context = _common_context(request, active_page="records")
        context.update({
            "sites": repo.list_sites(workspace_id),
            "form_values": values or {"log_date": datetime.now(SEOUL).date().isoformat()},
            "form_errors": errors or {},
            "global_error": global_error,
        })
        return context

    @app.get("/daily-log", response_class=HTMLResponse)
    async def daily_log(request: Request) -> HTMLResponse:
        values = {"log_date": datetime.now(SEOUL).date().isoformat()}
        if request.query_params.get('site_id'):
            site = _owned_site(repo, workspace_id, request.query_params['site_id'])
            values.update(site_id=str(site['id']), site_name=str(site['name']))
        return _render(request, "daily_log.html", daily_log_context(request, values=values))

    @app.post("/daily-log", response_class=HTMLResponse)
    async def daily_log_save(request: Request) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        selected_site = None
        if values.get('site_id'):
            selected_site = _owned_site(repo, workspace_id, values['site_id'])
            values['site_name'] = str(selected_site['name'])
        try:
            site_name = _text(values.get("site_name"), maximum=200, required=True)
        except ValueError:
            site_name = ""
            errors["site_name"] = "오늘 일한 현장 이름을 적어 주세요."
        try:
            log_date = _date(values.get("log_date", ""))
            if log_date is None:
                raise ValueError("required")
        except ValueError:
            log_date = None
            errors["log_date"] = "작업 날짜를 확인해 주세요."
        field_specs = (
            ("work_done", "오늘 한 일"),
            ("people", "함께 일한 사람"),
            ("vehicles", "차량·장비"),
            ("waste", "폐기물·고물"),
            ("money", "돈"),
            ("owner_words", "사장이 한 말"),
            ("unexpected", "예상과 달랐던 일"),
            ("remaining", "남은 일과 일정"),
            ("questions", "궁금하거나 이상했던 점"),
        )
        details: dict[str, str] = {}
        try:
            for key, _label in field_specs:
                details[key] = _text(values.get(key), maximum=5_000)
        except ValueError:
            errors["entry_text"] = "기록이 너무 깁니다. 중요한 내용을 중심으로 조금 줄여 주세요."
        try:
            entry_text = _text(values.get("entry_text"), maximum=20_000)
        except ValueError:
            entry_text = ""
            errors["entry_text"] = "오늘의 기록은 20,000자 이내로 적어 주세요."
        # 이전 버전에서 작성 중이던 항목별 입력도 잃지 않고 한 편의 원문으로 합친다.
        if not entry_text:
            entry_text = "\n".join(
                f"{label}: {details[key]}" for key, label in field_specs if details.get(key)
            )
        if not entry_text:
            errors["entry_text"] = "오늘 있었던 일을 편하게 적어 주세요."
        if selected_site is None and site_name:
            matches = [row for row in repo.list_sites(workspace_id) if str(row.get('name', '')).strip() == site_name]
            if len(matches) > 1:
                errors['site_name'] = '같은 이름의 현장이 여러 개입니다. 현장 목록에서 주소를 확인하고 해당 현장의 작업일지를 열어 주세요.'
            elif matches:
                selected_site = matches[0]
        if errors:
            return _render(request, "daily_log.html", daily_log_context(request, values=values, errors=errors), status_code=422)

        raw_lines = ["[오늘의 철거 기록]", f"날짜: {log_date}", f"현장: {site_name}", "기록:", entry_text]
        raw_text = "\n".join(raw_lines)
        original = _store_optional_original(current_settings, raw_text, kind="daily-demolition-log")
        try:
            site = selected_site
            if site is None:
                site = repo.create_site(
                    workspace_id, site_name, business_status="in_progress",
                    notes="오늘의 철거 기록에서 만든 현장", actor_type="user",
                )
            assert original is not None
            existing_source = next((row for row in repo.list_sources(workspace_id, site_id=site['id'])
                                    if row.get('content_hash_sha256') == original.sha256.lower()), None)
            source = existing_source or repo.create_source(
                workspace_id, "manual_input", original.sha256,
                f"local-original:{original.relative_path}", site_id=site["id"],
                original_name=original.path.name, mime_type="text/plain; charset=utf-8",
                byte_size=original.path.stat().st_size,
                captured_at=datetime.now(SEOUL).isoformat(timespec="minutes"),
                source_timezone="Asia/Seoul", processing_status="processed",
                privacy_level="sensitive", actor_type="user",
            )
            evidence = repo.create_evidence(
                source["id"], "whole_source", excerpt=raw_text[:2_000], actor_type="system",
            )
            repo.create_event(
                site["id"], "daily_work_log", f"{log_date} 철거 작업 기록",
                description=raw_text, occurred_at=f"{log_date}T18:00+09:00",
                time_precision="date", epistemic_type="fact", review_status="approved",
                evidence_ref=f"evidence:{evidence['id']}", actor_type="user",
            )
            evidence_ref = f"evidence:{evidence['id']}"
            direct_expenses = explicit_expenses(entry_text)
            existing_keys = {row['lineage_key'] for row in repo.list_for_site(site['id'], 'money_item')}
            for index, expense in enumerate(direct_expenses):
                key = f"daily-expense:{original.sha256}:{index}"
                if key in existing_keys:
                    continue
                repo.create_money_item(
                    site['id'], key, expense['text'][:300], expense['amount'],
                    expense['purpose'], 'confirmed', 'actual', 'outflow',
                    occurred_at=f'{log_date}T18:00+09:00',
                    notes=expense['text'], evidence_ref=evidence_ref,
                    epistemic_type='fact', review_status='approved', actor_type='system',
                    actor_id='explicit-daily-expense-v1',
                )
            for candidate in extract_candidates(raw_text):
                candidate_values = candidate.values
                if candidate.kind == 'money_item' and any(item['text'] in candidate.excerpt for item in direct_expenses):
                    continue
                if candidate.kind == "event":
                    repo.create_event(
                        site["id"], candidate_values["event_type"], candidate.title,
                        description=candidate_values["description"],
                        occurred_at=f"{log_date}T18:00+09:00", time_precision="date",
                        epistemic_type=candidate_values["epistemic_type"], review_status="pending",
                        evidence_ref=evidence_ref, actor_type="ai",
                    )
                elif candidate.kind == "task":
                    repo.create_task(
                        site["id"], candidate.title,
                        description=f"오늘의 철거 기록에서 정리한 후보: {candidate.excerpt}",
                        phase=candidate_values["phase"], priority=candidate_values["priority"],
                        epistemic_type=candidate_values["epistemic_type"], review_status="pending",
                        evidence_ref=evidence_ref, actor_type="ai",
                    )
                elif candidate.kind == "money_item":
                    repo.create_money_item(
                        site["id"], f"daily-log-{uuid4()}", candidate.title,
                        candidate_values["amount_krw"], candidate_values["purpose"],
                        candidate_values["progression"], candidate_values["actualness"],
                        candidate_values["direction"],
                        notes=f"오늘의 철거 기록에서 정리한 후보: {candidate.excerpt}",
                        epistemic_type=candidate_values["epistemic_type"], review_status="pending",
                        evidence_ref=evidence_ref, actor_type="ai",
                    )
                elif candidate.kind == "risk":
                    repo.create_risk(
                        site["id"], candidate_values["category"], candidate.title,
                        description=f"오늘의 철거 기록에서 정리한 후보: {candidate.excerpt}",
                        severity=candidate_values["severity"],
                        epistemic_type=candidate_values["epistemic_type"], review_status="pending",
                        evidence_ref=evidence_ref, actor_type="ai",
                    )
        except FieldBrainError as exc:
            if "source" not in locals():
                _cleanup_uncommitted(original)
            return _render(
                request, "daily_log.html",
                daily_log_context(request, values=values, errors={}, global_error=_friendly_core_error(exc)),
                status_code=422,
            )
        return RedirectResponse(f"/sites/{site['id']}?notice=daily_log_created#timeline", status_code=303)

    def schedule_page_context(request: Request, kind: str, values: dict[str, str] | None = None,
                              errors: dict[str, str] | None = None, global_error: str | None = None) -> dict[str, Any]:
        sites = repo.list_sites(workspace_id)
        active_page = "work_schedules" if kind == "work" else "estimate_visits"
        context = _common_context(request, active_page=active_page)
        schedule_rows = present_schedule_rows(
            repo.list_schedule_items(workspace_id, schedule_type=kind, review_status="approved"), sites
        )
        schedule_rows = (
            [row for row in schedule_rows if not row.get("is_past")]
            + list(reversed([row for row in schedule_rows if row.get("is_past")]))
        )
        context.update({
            "sites": sites,
            "schedule_rows": schedule_rows,
            "upcoming_schedule_rows": [row for row in schedule_rows if not row.get("is_past")],
            "past_schedule_rows": [row for row in schedule_rows if row.get("is_past")],
            "upcoming_schedule_count": sum(not row.get("is_past") for row in schedule_rows),
            "form_values": values or {},
            "form_errors": errors or {},
            "global_error": global_error,
        })
        if kind == "estimate_visit":
            estimate_visit_calendar_context(context)
        return context

    @app.get("/work-schedules", response_class=HTMLResponse)
    async def work_schedules(request: Request) -> HTMLResponse:
        return _render(request, "work_schedules.html", schedule_page_context(request, "work"))

    @app.get("/schedule-import", response_class=HTMLResponse)
    async def schedule_import_page(request: Request) -> HTMLResponse:
        context = _common_context(request, active_page="records")
        context.update({"import_result": None})
        return _render(request, "schedule_import.html", context)

    @app.post("/schedule-import", response_class=HTMLResponse)
    async def schedule_import_save(request: Request) -> HTMLResponse:
        form = await request.form()
        supplied = str(form.get("csrf_token") or "")
        expected = str(request.app.state.csrf_token)
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="요청 확인값이 만료됐습니다. 화면을 새로 열어 다시 저장해 주세요.")
        kakao_file = form.get("kakao_file")
        markdown_file = form.get("markdown_file")
        error: str | None = None
        kakao_text = markdown_text = ""
        kakao_present = isinstance(kakao_file, UploadFile) and bool(kakao_file.filename)
        markdown_present = isinstance(markdown_file, UploadFile) and bool(markdown_file.filename)
        if not kakao_present and not markdown_present:
            error = "카카오톡 TXT 또는 Markdown 파일을 하나 이상 선택해 주세요."
        else:
            try:
                kakao_bytes = await kakao_file.read(2_000_001) if kakao_present else b''
                markdown_bytes = await markdown_file.read(2_000_001) if markdown_present else b''
            finally:
                if isinstance(kakao_file, UploadFile):
                    await kakao_file.close()
                if isinstance(markdown_file, UploadFile):
                    await markdown_file.close()
            if len(kakao_bytes) > 2_000_000 or len(markdown_bytes) > 2_000_000:
                error = "각 파일은 2MB 이하여야 합니다."
            else:
                try:
                    kakao_text = kakao_bytes.decode("utf-8-sig")
                    markdown_text = markdown_bytes.decode("utf-8-sig")
                    if not kakao_text.strip() and not markdown_text.strip():
                        error = "선택한 파일에 내용이 없습니다. 대화나 일정이 들어 있는 자료를 선택해 주세요."
                except UnicodeDecodeError:
                    error = "UTF-8로 저장된 TXT와 Markdown 파일만 가져올 수 있습니다."
        result = None
        if error is None:
            try:
                result = import_schedule_candidates(
                    repo, workspace_id, current_settings.originals_dir,
                    kakao_text, markdown_text,
                    kakao_name=kakao_file.filename if kakao_present else "KakaoTalkChats.txt",
                    markdown_name=markdown_file.filename if markdown_present else "Field-Brain.md",
                )
            except (FieldBrainError, OSError, ValueError) as exc:
                error = "자료를 안전하게 가져오지 못했습니다. 원본 형식과 저장 공간을 확인해 주세요."
        context = _common_context(request, active_page="records")
        context.update({"import_result": result, "global_error": error,
                        "import_schedules": present_schedule_rows(result.schedule_items, repo.list_sites(workspace_id)) if result else []})
        if error:
            return _render(request, "schedule_import.html", context, status_code=422)
        return _render(request, "schedule_import.html", context)

    @app.post("/work-schedules", response_class=HTMLResponse)
    async def work_schedule_create(request: Request) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            site_id = _text(values.get("site_id"), maximum=80, required=True)
            site = _owned_site(repo, workspace_id, site_id)
        except (ValueError, HTTPException):
            site_id, site = "", None
            errors["site_id"] = "작업할 현장을 선택해 주세요."
        try:
            start_at = _local_datetime(values.get("start_at", ""))
            if not start_at:
                raise ValueError("required")
        except ValueError:
            start_at = None
            errors["start_at"] = "작업 시작 날짜와 시간을 입력해 주세요."
        try:
            end_at = _local_datetime(values.get("end_at", ""))
        except ValueError:
            end_at = None
            errors["end_at"] = "끝나는 날짜와 시간을 올바르게 입력해 주세요."
        if start_at and end_at and end_at < start_at:
            errors["end_at"] = "끝나는 시간은 시작 시간보다 뒤여야 합니다."
        try:
            summary = _text(values.get("summary"), maximum=3_000, required=True)
            participants = _text(values.get("participants"), maximum=1_000)
            notes = _text(values.get("notes"), maximum=3_000)
        except ValueError:
            summary = participants = notes = ""
            errors["summary"] = "작업 내용을 3,000자 이내로 입력해 주세요."
        if errors:
            return _render(request, "work_schedules.html",
                           schedule_page_context(request, "work", values, errors, "표시된 항목을 확인해 주세요."),
                           status_code=422)
        try:
            repo.create_schedule_item(workspace_id, "work", str(site["name"]), str(start_at), summary,
                                      site_id=site_id, end_at=end_at, participants_text=participants or None,
                                      notes=notes or None, actor_type="user")
        except FieldBrainError as exc:
            return _render(request, "work_schedules.html",
                           schedule_page_context(request, "work", values, {}, _friendly_core_error(exc)),
                           status_code=422)
        return RedirectResponse("/work-schedules?notice=work_schedule_created", status_code=303)

    def estimate_visit_calendar_context(context: dict[str, Any], month: str = "", day: str = "") -> None:
        today = datetime.now(SEOUL).date()
        try:
            visible_month = datetime.strptime(month, "%Y-%m").date().replace(day=1) if month else today.replace(day=1)
        except ValueError:
            visible_month = today.replace(day=1)
        previous_month = (visible_month - timedelta(days=1)).replace(day=1)
        next_month = (visible_month.replace(day=28) + timedelta(days=4)).replace(day=1)
        scheduled_rows = [
            row for row in context["schedule_rows"] if row.get("business_status") == "scheduled"
        ]
        rows_by_date: dict[str, list[dict[str, Any]]] = {}
        for row in scheduled_rows:
            rows_by_date.setdefault(str(row.get("date_key") or ""), []).append(row)
        month_prefix = visible_month.strftime("%Y-%m")
        requested_day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") else ""
        if not requested_day.startswith(month_prefix):
            requested_day = ""
        if not requested_day:
            month_visit_days = [key for key in sorted(rows_by_date) if key.startswith(month_prefix)]
            today_key = today.isoformat()
            if today.strftime("%Y-%m") == month_prefix:
                requested_day = (
                    today_key
                    if rows_by_date.get(today_key)
                    else next(
                        (key for key in month_visit_days if key >= today_key),
                        month_visit_days[0] if month_visit_days else today_key,
                    )
                )
            else:
                requested_day = month_visit_days[0] if month_visit_days else visible_month.isoformat()
        weeks = []
        for week in calendar.Calendar(firstweekday=6).monthdatescalendar(visible_month.year, visible_month.month):
            cells = []
            for cell_date in week:
                date_key = cell_date.isoformat()
                count = len(rows_by_date.get(date_key, []))
                cells.append({
                    "date_key": date_key, "day": cell_date.day,
                    "in_month": cell_date.month == visible_month.month,
                    "count": count, "is_today": cell_date == today,
                    "is_selected": date_key == requested_day,
                })
            weeks.append(cells)
        context.update({
            "calendar_title": f"{visible_month.year}년 {visible_month.month}월",
            "calendar_month": month_prefix,
            "calendar_previous": previous_month.strftime("%Y-%m"),
            "calendar_next": next_month.strftime("%Y-%m"),
            "calendar_weeks": weeks,
            "selected_date": requested_day,
            "selected_day_label": schedule_day_label(requested_day),
            "selected_visit_rows": rows_by_date.get(requested_day, []),
        })

    @app.get("/estimate-visits", response_class=HTMLResponse)
    async def estimate_visits(request: Request, month: str = "", day: str = "") -> HTMLResponse:
        context = schedule_page_context(request, "estimate_visit")
        estimate_visit_calendar_context(context, month, day)
        return _render(request, "estimate_visits.html", context)

    @app.get("/field-reference", response_class=HTMLResponse)
    @app.get("/estimate-standards", response_class=HTMLResponse)
    async def estimate_standards(request: Request, archived: int = 0, q: str = "") -> HTMLResponse:
        rows = repo.list_estimate_standards(workspace_id, include_archived=bool(archived))
        query = q.strip().lower()
        if query:
            rows = [row for row in rows if query in " ".join(
                str(row.get(key) or "") for key in ("title", "rule_text", "rationale", "source_note")
            ).lower()]
        labels = {"process": "작업·견적 요령", "labor": "인건비", "equipment": "장비",
                  "waste": "폐기물", "margin": "견적·마진", "restoration": "원상복구",
                  "condition": "주의사항", "other": "현장 용어·기타"}
        groups: list[dict[str, Any]] = []
        for key in labels:
            items = [dict(row, category_label=labels[key]) for row in rows if row["category"] == key]
            if items:
                groups.append({"key": key, "label": labels[key], "items": items})
        context = _common_context(request, active_page="business")
        context.update({"groups": groups, "show_archived": bool(archived), "q": q,
                        "form_values": {}, "form_errors": {}})
        return _render(request, "estimate_standards.html", context)

    @app.post("/estimate-standards", response_class=HTMLResponse)
    async def estimate_standard_create(request: Request) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            title = _text(values.get("title"), maximum=200, required=True)
            rule_text = _text(values.get("rule_text"), maximum=10_000, required=True)
            rationale = _text(values.get("rationale"), maximum=5_000)
            source_note = _text(values.get("source_note"), maximum=2_000)
            repo.create_estimate_standard(workspace_id, category=values.get("category", "other"),
                                          title=title, rule_text=rule_text, rationale=rationale or None,
                                          source_note=source_note or None)
        except (ValueError, FieldBrainError):
            errors["form"] = "분류, 기준 이름, 기준 내용을 확인해 주세요."
        if errors:
            rows = repo.list_estimate_standards(workspace_id)
            labels = {"process": "작업·견적 요령", "labor": "인건비", "equipment": "장비", "waste": "폐기물", "margin": "견적·마진", "restoration": "원상복구", "condition": "주의사항", "other": "현장 용어·기타"}
            groups = [{"key": key, "label": label, "items": [dict(r, category_label=label) for r in rows if r["category"] == key]} for key, label in labels.items() if any(r["category"] == key for r in rows)]
            context = _common_context(request, active_page="business")
            context.update({"groups": groups, "show_archived": False, "q": "", "form_values": values, "form_errors": errors, "global_error": errors["form"]})
            return _render(request, "estimate_standards.html", context, status_code=422)
        return RedirectResponse("/field-reference?notice=estimate_standard_created", status_code=303)

    @app.post("/estimate-standards/{standard_id}/active", response_class=HTMLResponse)
    async def estimate_standard_active(request: Request, standard_id: str) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        try:
            row = repo.set_estimate_standard_active(workspace_id, standard_id, is_active=values.get("is_active") == "1")
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="견적 기준을 찾을 수 없습니다.") from exc
        notice = "estimate_standard_restored" if row["is_active"] else "estimate_standard_archived"
        return RedirectResponse(f"/field-reference?archived=1&notice={notice}", status_code=303)

    @app.post("/estimate-visits", response_class=HTMLResponse)
    async def estimate_visit_create(request: Request) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            start_at = _local_datetime(values.get("start_at", ""))
            if not start_at:
                raise ValueError("required")
        except ValueError:
            start_at = None
            errors["start_at"] = "방문 날짜와 시간을 입력해 주세요."
        for field, limit, message in (
            ("customer_name", 200, "고객 이름이나 구분할 이름을 입력해 주세요."),
            ("address", 1_000, "방문 주소를 입력해 주세요."),
            ("summary", 3_000, "요청받은 철거 내용을 입력해 주세요."),
        ):
            try:
                values[field] = _text(values.get(field), maximum=limit, required=True)
            except ValueError:
                errors[field] = message
        try:
            contact = _text(values.get("contact"), maximum=300)
            notes = _text(values.get("notes"), maximum=3_000)
        except ValueError:
            contact = notes = ""
            errors["notes"] = "메모를 3,000자 이내로 입력해 주세요."
        if errors:
            return _render(request, "estimate_visits.html",
                           schedule_page_context(request, "estimate_visit", values, errors, "표시된 항목을 확인해 주세요."),
                           status_code=422)
        try:
            repo.create_schedule_item(
                workspace_id, "estimate_visit", values["customer_name"], str(start_at), values["summary"],
                customer_name=values["customer_name"], customer_contact=contact or None,
                address_text=values["address"], notes=notes or None, actor_type="user",
            )
        except FieldBrainError as exc:
            return _render(request, "estimate_visits.html",
                           schedule_page_context(request, "estimate_visit", values, {}, _friendly_core_error(exc)),
                           status_code=422)
        return RedirectResponse("/estimate-visits?notice=estimate_visit_created", status_code=303)

    @app.get("/schedules/{schedule_id}", response_class=HTMLResponse)
    async def schedule_detail(request: Request, schedule_id: str) -> HTMLResponse:
        try:
            item = repo.get_schedule_item(schedule_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.") from exc
        if item.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
        sites = repo.list_sites(workspace_id)
        presented = present_schedule_rows([item], sites)[0]
        active_page = "work_schedules" if item.get("schedule_type") == "work" else "estimate_visits"
        context = _common_context(request, active_page=active_page)
        context.update({
            "schedule": presented,
            "estimate_result": repo.get_estimate_visit_result(schedule_id) if item.get("schedule_type") == "estimate_visit" else None,
            "estimate_form_values": {},
            "estimate_form_errors": {},
        })
        return _render(request, "schedule_detail.html", context)

    @app.post("/schedules/{schedule_id}/reschedule", response_class=HTMLResponse)
    async def schedule_reschedule(request: Request, schedule_id: str) -> HTMLResponse:
        item = repo.get_schedule_item(schedule_id)
        if item.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
        values = await _form_dict(request)
        _validate_csrf(request, values)
        try:
            start_at = _local_datetime(values.get("start_at", ""))
            end_at = _local_datetime(values.get("end_at", ""))
            reason = _text(values.get("reason"), maximum=2_000, required=True)
            if start_at is None:
                raise ValueError("required")
            precision = "range" if end_at else "exact"
            repo.reschedule_item(
                schedule_id, start_at=start_at, end_at=end_at,
                time_precision=precision, reason=reason,
            )
        except (ValueError, FieldBrainError) as exc:
            raise HTTPException(
                status_code=422,
                detail="새 날짜·시간과 변경 이유를 확인해 주세요. 끝 시간은 시작보다 뒤여야 합니다.",
            ) from exc
        return RedirectResponse(f"/schedules/{schedule_id}?notice=schedule_rescheduled", status_code=303)

    @app.post("/schedules/{schedule_id}/cancel", response_class=HTMLResponse)
    async def schedule_cancel(request: Request, schedule_id: str) -> HTMLResponse:
        item = repo.get_schedule_item(schedule_id)
        if item.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
        values = await _form_dict(request)
        _validate_csrf(request, values)
        try:
            reason = _text(values.get("reason"), maximum=2_000, required=True)
            cancelled = values.get("cancelled") != "0"
            repo.set_schedule_cancelled(schedule_id, cancelled=cancelled, reason=reason)
        except (ValueError, FieldBrainError) as exc:
            raise HTTPException(status_code=422, detail="일정 상태를 바꾼 이유를 입력해 주세요.") from exc
        notice = "schedule_cancelled" if cancelled else "schedule_restored"
        return RedirectResponse(f"/schedules/{schedule_id}?notice={notice}", status_code=303)

    @app.post("/schedules/{schedule_id}/agreement", response_class=HTMLResponse)
    async def visit_agreement(request: Request, schedule_id: str) -> HTMLResponse:
        try:
            item = repo.get_schedule_item(schedule_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail='견적 방문을 찾을 수 없습니다.') from exc
        if item.get('workspace_id') != workspace_id or item.get('schedule_type') != 'estimate_visit':
            raise HTTPException(status_code=404, detail='견적 방문을 찾을 수 없습니다.')
        values = await _form_dict(request)
        _validate_csrf(request, values)
        try:
            site_id = repo.apply_visit_agreement(workspace_id, schedule_id, values.get('agreement_note', ''))
        except (ValueError, FieldBrainError) as exc:
            context = _common_context(request, active_page='estimate_visits')
            context.update({
                'schedule': present_schedule_rows([item], repo.list_sites(workspace_id))[0],
                'estimate_result': repo.get_estimate_visit_result(schedule_id),
                'estimate_form_values': {}, 'estimate_form_errors': {},
                'agreement_note': values.get('agreement_note', ''), 'agreement_error': str(exc),
            })
            return _render(request, 'schedule_detail.html', context, status_code=422)
        return RedirectResponse(f'/sites/{site_id}?notice=agreement_applied', status_code=303)

    @app.post("/schedules/{schedule_id}/estimate-result", response_class=HTMLResponse)
    async def estimate_visit_result_save(request: Request, schedule_id: str) -> HTMLResponse:
        try:
            item = repo.get_schedule_item(schedule_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.") from exc
        if item.get("workspace_id") != workspace_id or item.get("schedule_type") != "estimate_visit":
            raise HTTPException(status_code=404, detail="견적 방문 일정을 찾을 수 없습니다.")
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            customer_requests = _text(values.get("customer_requests"), maximum=20_000, required=True)
        except ValueError:
            customer_requests = ""
            errors["customer_requests"] = "고객이 요청한 철거·복구 범위를 적어 주세요."
        text_fields: dict[str, str] = {}
        try:
            for key, maximum in (
                ("work_plan", 20_000), ("equipment_plan", 10_000), ("waste_plan", 10_000),
                ("restoration_plan", 10_000), ("conditions_text", 10_000),
            ):
                text_fields[key] = _text(values.get(key), maximum=maximum)
        except ValueError:
            errors["details"] = "견적 결과 내용이 너무 깁니다. 항목별로 나누어 적어 주세요."
        amounts: dict[str, int | None] = {}
        for key in ("labor_cost_krw", "equipment_cost_krw", "waste_cost_krw", "restoration_cost_krw", "total_quote_krw"):
            try:
                amounts[key] = _optional_amount(values.get(key, ""))
            except ValueError:
                errors[key] = "금액은 숫자로만 입력하고, 모르면 비워 두세요."
        sites = repo.list_sites(workspace_id)
        presented = present_schedule_rows([item], sites)[0]
        if errors:
            context = _common_context(request, active_page="estimate_visits")
            context.update({
                "schedule": presented, "estimate_result": repo.get_estimate_visit_result(schedule_id),
                "estimate_form_values": values, "estimate_form_errors": errors,
            })
            return _render(request, "schedule_detail.html", context, status_code=422)
        try:
            repo.save_estimate_visit_result(
                schedule_id, customer_requests=customer_requests,
                work_plan=text_fields.get("work_plan"), labor_cost_krw=amounts["labor_cost_krw"],
                equipment_plan=text_fields.get("equipment_plan"), equipment_cost_krw=amounts["equipment_cost_krw"],
                waste_plan=text_fields.get("waste_plan"), waste_cost_krw=amounts["waste_cost_krw"],
                restoration_plan=text_fields.get("restoration_plan"), restoration_cost_krw=amounts["restoration_cost_krw"],
                total_quote_krw=amounts["total_quote_krw"], conditions_text=text_fields.get("conditions_text"),
            )
        except FieldBrainError as exc:
            context = _common_context(request, active_page="estimate_visits")
            context.update({
                "schedule": presented, "estimate_result": repo.get_estimate_visit_result(schedule_id),
                "estimate_form_values": values, "estimate_form_errors": {},
                "global_error": _friendly_core_error(exc),
            })
            return _render(request, "schedule_detail.html", context, status_code=422)
        return RedirectResponse(f"/schedules/{schedule_id}?notice=estimate_result_saved#estimate-result", status_code=303)

    @app.get("/sites", response_class=HTMLResponse)
    async def sites(request: Request, q: str = "") -> HTMLResponse:
        query = q.strip()[:120]
        dashboard_data = build_dashboard_context(repo, workspace_id)
        rows = dashboard_data["sites"]
        if query:
            needle = query.casefold()
            rows = [
                row
                for row in rows
                if needle in str(row.get("name") or "").casefold()
                or needle in str(row.get("address_display") or "").casefold()
            ]
        context = _common_context(request, active_page="sites")
        context.update({"q": query, "sites": rows})
        return _render(request, "sites.html", context)

    @app.get("/sites/new", response_class=HTMLResponse)
    async def site_new(request: Request) -> HTMLResponse:
        context = _common_context(request, active_page="sites")
        context.update({"form_values": {}, "form_errors": {}})
        return _render(request, "site_new.html", context)

    @app.post("/sites", response_class=HTMLResponse)
    async def site_create(request: Request) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            name = _text(values.get("name"), maximum=120, required=True)
        except ValueError:
            name = ""
            errors["name"] = "현장명을 120자 이내로 입력해 주세요."
        try:
            address = _text(values.get("address"), maximum=240)
        except ValueError:
            address = ""
            errors["address"] = "주소는 240자 이내로 입력해 주세요."
        stage = values.get("stage", "inquiry")
        if stage not in SITE_STAGE_MAP:
            errors["stage"] = "현재 단계를 다시 선택해 주세요."
        try:
            start_date = _date(values.get("start_date", ""))
        except ValueError:
            start_date = None
            errors["start_date"] = "시작일을 올바른 날짜로 입력해 주세요."
        try:
            customer_name = _text(values.get("customer_name"), maximum=120)
            customer_contact = _text(values.get("customer_contact"), maximum=160)
            source_note = _text(values.get("source_note"), maximum=5_000)
        except ValueError:
            customer_name = customer_contact = source_note = ""
            errors["source_note"] = "입력 내용이 너무 깁니다."
        if customer_contact and not customer_name:
            errors["customer_name"] = "연락처 메모를 남기려면 고객 이름도 입력해 주세요."
        if errors:
            context = _common_context(request, active_page="sites")
            context.update(
                {
                    "form_values": values,
                    "form_errors": errors,
                    "global_error": "표시된 항목을 확인해 주세요.",
                }
            )
            return _render(request, "site_new.html", context, status_code=422)

        original = _store_optional_original(current_settings, source_note, kind="first-inquiry")
        try:
            site = repo.create_site(
                workspace_id,
                name,
                address_text=address or None,
                business_status=SITE_STAGE_MAP[stage],
                planned_start_at=start_date,
                industry_data={"ui_stage": stage},
                actor_type="user",
            )
            if customer_name:
                person = repo.create_person(
                    workspace_id,
                    customer_name,
                    notes=customer_contact or None,
                    privacy_level="sensitive" if customer_contact else "private",
                    actor_type="user",
                )
                repo.create_site_role(
                    site["id"],
                    person["id"],
                    "client",
                    role_label="고객",
                    is_primary_contact=True,
                    evidence_ref=original.evidence_ref if original else None,
                    actor_type="user",
                )
            if source_note:
                repo.create_event(
                    site["id"],
                    "inquiry",
                    "첫 문의 원문 보존",
                    description="첫 문의 내용을 원문 파일로 보존했습니다. 작업 범위는 검토 후 확정합니다.",
                    occurred_at=datetime.now(SEOUL).isoformat(timespec="minutes"),
                    time_precision="exact",
                    epistemic_type="claim",
                    review_status="pending",
                    evidence_ref=original.evidence_ref if original else None,
                    actor_type="user",
                )
        except FieldBrainError as exc:
            if "site" not in locals():
                _cleanup_uncommitted(original)
            context = _common_context(request, active_page="sites")
            context.update(
                {
                    "form_values": values,
                    "form_errors": {},
                    "global_error": _friendly_core_error(exc),
                }
            )
            return _render(request, "site_new.html", context, status_code=422)
        return RedirectResponse(f"/sites/{site['id']}?notice=site_created", status_code=303)

    @app.get("/sites/{site_id}", response_class=HTMLResponse)
    async def site_detail(request: Request, site_id: str) -> HTMLResponse:
        site = _owned_site(repo, workspace_id, site_id)
        context = _common_context(request, active_page="sites")
        detail = build_site_context(repo, workspace_id, site)
        detail.pop("_raw_money", None)
        context.update(detail)
        context.update({"forms": {}, "open_form": ""})
        return _render(request, "site_detail.html", context)

    @app.post("/sites/{site_id}/capture", response_class=HTMLResponse)
    async def integrated_capture(request: Request, site_id: str) -> HTMLResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        capture_kind = values.get("capture_kind", "work_note")
        kind_map = {
            "work_note": ("manual_input", "field-note"),
            "call_transcript": ("transcript", "call-transcript"),
            "message": ("message_export", "message"),
        }
        if capture_kind not in kind_map:
            errors["capture_kind"] = "기록 종류를 다시 선택해 주세요."
        try:
            raw_text = _text(values.get("raw_text"), maximum=20_000, required=True)
        except ValueError:
            raw_text = ""
            errors["raw_text"] = "현장에서 있었던 일을 20,000자 이내로 적어 주세요."
        candidates = extract_candidates(raw_text) if raw_text else []
        if raw_text and not candidates:
            errors["raw_text"] = "정리할 수 있는 내용을 찾지 못했습니다. 문장으로 조금 더 자세히 적어 주세요."
        if errors:
            return _site_detail_response(request, site_id, form_name="capture", values=values, errors=errors)

        source_type, original_kind = kind_map[capture_kind]
        original = _store_optional_original(current_settings, raw_text, kind=original_kind)
        try:
            assert original is not None
            source = repo.create_source(
                workspace_id,
                source_type,
                original.sha256,
                f"local-original:{original.relative_path}",
                site_id=site_id,
                original_name=original.path.name,
                mime_type="text/plain; charset=utf-8",
                byte_size=original.path.stat().st_size,
                captured_at=datetime.now(SEOUL).isoformat(timespec="minutes"),
                source_timezone="Asia/Seoul",
                processing_status="processed",
                privacy_level="sensitive",
                actor_type="user",
            )
            for candidate in candidates:
                excerpt = candidate.excerpt[:2_000]
                text_start = raw_text.find(candidate.excerpt)
                if text_start < 0:
                    text_start = 0
                text_end = min(len(raw_text), text_start + len(candidate.excerpt))
                evidence = repo.create_evidence(
                    source["id"],
                    "text_range",
                    text_start=text_start,
                    text_end=text_end,
                    excerpt=excerpt,
                    actor_type="system",
                )
                evidence_ref = f"evidence:{evidence['id']}"
                values_for_candidate = candidate.values
                if candidate.kind == "event":
                    repo.create_event(
                        site_id,
                        values_for_candidate["event_type"],
                        candidate.title,
                        description=values_for_candidate["description"],
                        occurred_at=datetime.now(SEOUL).isoformat(timespec="minutes"),
                        time_precision="exact",
                        epistemic_type=values_for_candidate["epistemic_type"],
                        review_status="pending",
                        evidence_ref=evidence_ref,
                        actor_type="ai",
                    )
                elif candidate.kind == "task":
                    repo.create_task(
                        site_id,
                        candidate.title,
                        description=f"원문에서 정리한 후보: {candidate.excerpt}",
                        phase=values_for_candidate["phase"],
                        priority=values_for_candidate["priority"],
                        epistemic_type=values_for_candidate["epistemic_type"],
                        review_status="pending",
                        evidence_ref=evidence_ref,
                        actor_type="ai",
                    )
                elif candidate.kind == "money_item":
                    repo.create_money_item(
                        site_id,
                        f"capture-{uuid4()}",
                        candidate.title,
                        values_for_candidate["amount_krw"],
                        values_for_candidate["purpose"],
                        values_for_candidate["progression"],
                        values_for_candidate["actualness"],
                        values_for_candidate["direction"],
                        notes=f"원문에서 정리한 후보: {candidate.excerpt}",
                        epistemic_type=values_for_candidate["epistemic_type"],
                        review_status="pending",
                        evidence_ref=evidence_ref,
                        actor_type="ai",
                    )
                elif candidate.kind == "risk":
                    repo.create_risk(
                        site_id,
                        values_for_candidate["category"],
                        candidate.title,
                        description=f"원문에서 정리한 후보: {candidate.excerpt}",
                        severity=values_for_candidate["severity"],
                        epistemic_type=values_for_candidate["epistemic_type"],
                        review_status="pending",
                        evidence_ref=evidence_ref,
                        actor_type="ai",
                    )
        except FieldBrainError as exc:
            if "source" not in locals():
                _cleanup_uncommitted(original)
            return _site_detail_response(
                request,
                site_id,
                form_name="capture",
                values=values,
                errors={},
                global_error=_friendly_core_error(exc),
            )
        return RedirectResponse(f"/sites/{site_id}?notice=capture_created", status_code=303)

    @app.post("/sites/{site_id}/people", response_class=HTMLResponse)
    async def person_create(request: Request, site_id: str) -> HTMLResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            person_name = _text(values.get("person_name"), maximum=120, required=True)
        except ValueError:
            person_name = ""
            errors["person_name"] = "이름을 120자 이내로 입력해 주세요."
        role_name = values.get("role_name", "other")
        if role_name not in ROLE_MAP:
            errors["role_name"] = "역할을 다시 선택해 주세요."
        try:
            contact_note = _text(values.get("contact_note"), maximum=160)
            source_note = _text(values.get("source_note"), maximum=2_000)
        except ValueError:
            contact_note = source_note = ""
            errors["source_note"] = "근거 메모는 2,000자 이내로 입력해 주세요."
        if errors:
            return _site_detail_response(request, site_id, form_name="person", values=values, errors=errors)

        original = _store_optional_original(current_settings, source_note, kind="person-role")
        try:
            person = repo.create_person(
                workspace_id,
                person_name,
                notes=contact_note or None,
                privacy_level="sensitive" if contact_note else "private",
                actor_type="user",
            )
            role_type, role_label = ROLE_MAP[role_name]
            repo.create_site_role(
                site_id,
                person["id"],
                role_type,
                role_label=role_label,
                evidence_ref=original.evidence_ref if original else None,
                actor_type="user",
            )
        except FieldBrainError as exc:
            if "person" not in locals():
                _cleanup_uncommitted(original)
            return _site_detail_response(
                request,
                site_id,
                form_name="person",
                values=values,
                errors={},
                global_error=_friendly_core_error(exc),
            )
        return RedirectResponse(f"/sites/{site_id}?notice=person_created#people", status_code=303)

    @app.post("/sites/{site_id}/tasks", response_class=HTMLResponse)
    async def task_create(request: Request, site_id: str) -> HTMLResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            title = _text(values.get("title"), maximum=240, required=True)
        except ValueError:
            title = ""
            errors["title"] = "해야 할 일을 240자 이내로 입력해 주세요."
        phase = values.get("phase", "execution")
        if phase not in TASK_PHASE_MAP:
            errors["phase"] = "업무 단계를 다시 선택해 주세요."
        status = values.get("status", "open")
        if status not in TASK_STATUS_MAP:
            errors["status"] = "진행 상태를 다시 선택해 주세요."
        try:
            due_date = _date(values.get("due_date", ""))
        except ValueError:
            due_date = None
            errors["due_date"] = "완료 예정일을 올바르게 입력해 주세요."
        try:
            notes = _text(values.get("notes"), maximum=3_000)
            source_note = _text(values.get("source_note"), maximum=500)
        except ValueError:
            notes = source_note = ""
            errors["notes"] = "메모가 너무 깁니다."
        review_status = values.get("review_status", "pending")
        if review_status not in {"pending", "approved"}:
            errors["review_status"] = "검토 상태를 다시 선택해 주세요."
        assignee_id = values.get("assignee_person_id", "").strip() or None
        if assignee_id:
            role_person_ids = {
                str(row["person_id"]) for row in repo.list_for_site(site_id, "role")
            }
            if assignee_id not in role_person_ids:
                errors["assignee_person_id"] = "이 현장에 연결된 담당자를 선택해 주세요."
        if errors:
            return _site_detail_response(request, site_id, form_name="task", values=values, errors=errors)

        original = _store_optional_original(current_settings, source_note, kind="task-source")
        try:
            repo.create_task(
                site_id,
                title,
                description=notes or None,
                phase=TASK_PHASE_MAP[phase],
                industry_phase=phase,
                assignee_person_id=assignee_id,
                due_at=due_date,
                business_status=TASK_STATUS_MAP[status],
                blocked_reason="상대방 응답 대기" if status == "waiting" else None,
                epistemic_type="decision",
                review_status=review_status,
                evidence_ref=original.evidence_ref if original else None,
                actor_type="user",
            )
        except FieldBrainError as exc:
            _cleanup_uncommitted(original)
            return _site_detail_response(
                request, site_id, form_name="task", values=values, errors={},
                global_error=_friendly_core_error(exc),
            )
        return RedirectResponse(f"/sites/{site_id}?notice=task_created#tasks", status_code=303)

    @app.post("/sites/{site_id}/money", response_class=HTMLResponse)
    async def money_create(request: Request, site_id: str) -> HTMLResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        purpose = values.get("purpose", "labor_cost")
        if purpose not in MONEY_PURPOSES:
            errors["purpose"] = "돈의 목적을 다시 선택해 주세요."
        try:
            amount = _optional_amount(values.get("amount", ""))
        except ValueError:
            amount = None
            errors["amount"] = "금액은 0 이상의 원 단위 숫자로 입력해 주세요."
        try:
            occurred_on = _date(values.get("occurred_on", ""))
        except ValueError:
            occurred_on = None
            errors["occurred_on"] = "기준 날짜를 올바르게 입력해 주세요."
        try:
            note = _text(values.get("note"), maximum=3_000, required=True)
            source_note = _text(values.get("source_note"), maximum=2_000)
        except ValueError:
            note = source_note = ""
            errors["note"] = "금액 내용을 3,000자 이내로 입력해 주세요."
        actualness = values.get("actualness", "estimated")
        if actualness not in {"estimated", "actual"}:
            errors["actualness"] = "예상 또는 실제를 다시 선택해 주세요."
        progression = values.get("progression", "candidate")
        if progression not in {"candidate", "offered", "agreed", "claimed", "confirmed", "settled"}:
            errors["progression"] = "돈 진행 상태를 다시 선택해 주세요."
        epistemic = values.get("epistemic_status", "claim")
        if epistemic not in {"fact", "claim", "inference", "decision"}:
            errors["epistemic_status"] = "정보 성격을 다시 선택해 주세요."
        review_status = values.get("review_status", "pending")
        if review_status not in {"pending", "approved"}:
            errors["review_status"] = "검토 상태를 다시 선택해 주세요."
        if review_status == "approved" and epistemic == "inference" and not source_note:
            errors["source_note"] = "계산·추정을 확정하려면 계산 근거나 원문 메모를 남겨 주세요."
        if errors:
            return _site_detail_response(request, site_id, form_name="money", values=values, errors=errors)

        requested_direction = values.get("direction", "out")
        if purpose in INFLOW_PURPOSES:
            direction = "inflow"
        elif purpose in OUTFLOW_PURPOSES:
            direction = "outflow"
        elif purpose == "discount":
            direction = "neutral"
        else:
            direction = "inflow" if requested_direction == "in" else "outflow"
        original = _store_optional_original(current_settings, source_note, kind="money-source")
        try:
            repo.create_money_item(
                site_id,
                f"manual-{uuid4()}",
                note[:300],
                amount,
                purpose,
                progression,
                actualness,
                direction,
                notes=note,
                occurred_at=occurred_on,
                epistemic_type=epistemic,
                review_status=review_status,
                evidence_ref=original.evidence_ref if original else None,
                actor_type="user",
            )
        except FieldBrainError as exc:
            _cleanup_uncommitted(original)
            return _site_detail_response(
                request, site_id, form_name="money", values=values, errors={},
                global_error=_friendly_core_error(exc),
            )
        return RedirectResponse(f"/sites/{site_id}?notice=money_created#money", status_code=303)

    @app.post("/sites/{site_id}/cost-completeness")
    async def cost_completeness_save(request: Request, site_id: str) -> RedirectResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        completeness = values.get("completeness", "")
        reason = values.get("reason", "").strip()
        if completeness not in {"unknown", "partial", "complete"}:
            raise HTTPException(status_code=422, detail="실행비 입력 상태를 다시 선택해 주세요.")
        if not reason or len(reason) > 500:
            raise HTTPException(status_code=422, detail="상태를 바꾸는 이유를 500자 이내로 입력해 주세요.")
        try:
            repo.set_site_cost_completeness(
                site_id,
                completeness,
                reason=reason,
                actor_type="user",
            )
        except FieldBrainError as exc:
            raise HTTPException(status_code=422, detail=_friendly_core_error(exc)) from exc
        return RedirectResponse(
            f"/sites/{site_id}?notice=cost_scope_saved#money",
            status_code=303,
        )

    @app.post("/sites/{site_id}/risks", response_class=HTMLResponse)
    async def risk_create(request: Request, site_id: str) -> HTMLResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        try:
            title = _text(values.get("title"), maximum=240, required=True)
            response = _text(values.get("response"), maximum=3_000)
            source_note = _text(values.get("source_note"), maximum=2_000)
        except ValueError:
            title = response = source_note = ""
            errors["title"] = "위험 내용을 240자 이내로 입력해 주세요."
        category = values.get("category", "scope")
        if category not in RISK_CATEGORY_MAP:
            errors["category"] = "위험 분류를 다시 선택해 주세요."
        severity = values.get("severity", "medium")
        if severity not in {"low", "medium", "high", "critical"}:
            errors["severity"] = "심각도를 다시 선택해 주세요."
        review_status = values.get("review_status", "pending")
        if review_status not in {"pending", "approved"}:
            errors["review_status"] = "검토 상태를 다시 선택해 주세요."
        if values.get("estimated_additional_cost", "").strip():
            errors["estimated_additional_cost"] = "추가비는 돈 기록에 별도로 남겨 주세요. 중복 장부를 막기 위한 임시 제한입니다."
        if errors:
            return _site_detail_response(request, site_id, form_name="risk", values=values, errors=errors)

        evidence_text = source_note
        if review_status == "approved" and not evidence_text:
            evidence_text = f"사용자가 직접 확인한 위험: {title}\n대응: {response or '미정'}"
        original = _store_optional_original(current_settings, evidence_text, kind="risk-source")
        try:
            repo.create_risk(
                site_id,
                RISK_CATEGORY_MAP[category],
                title,
                industry_category=category,
                severity=severity,
                response_plan=response or None,
                epistemic_type="inference",
                review_status=review_status,
                evidence_ref=original.evidence_ref if original else None,
                actor_type="user",
            )
        except FieldBrainError as exc:
            _cleanup_uncommitted(original)
            return _site_detail_response(
                request, site_id, form_name="risk", values=values, errors={},
                global_error=_friendly_core_error(exc),
            )
        return RedirectResponse(f"/sites/{site_id}?notice=risk_created#risks", status_code=303)

    @app.post("/sites/{site_id}/timeline", response_class=HTMLResponse)
    async def timeline_create(request: Request, site_id: str) -> HTMLResponse:
        _owned_site(repo, workspace_id, site_id)
        values = await _form_dict(request)
        _validate_csrf(request, values)
        errors: dict[str, str] = {}
        kind = values.get("kind", "work")
        if kind not in {"call", "message", "visit", "quote", "decision", "work", "payment", "note"}:
            errors["kind"] = "기록 종류를 다시 선택해 주세요."
        try:
            occurred_at = _local_datetime(values.get("occurred_at", ""))
        except ValueError:
            occurred_at = None
            errors["occurred_at"] = "날짜와 시간을 올바르게 입력해 주세요."
        try:
            title = _text(values.get("title"), maximum=240, required=True)
            summary = _text(values.get("summary"), maximum=5_000, required=True)
            raw_text = _text(values.get("raw_text"), maximum=20_000)
        except ValueError:
            title = summary = raw_text = ""
            errors["summary"] = "제목과 정리 메모를 입력해 주세요."
        epistemic = values.get("epistemic_status", "claim")
        if epistemic not in {"fact", "claim", "inference", "decision", "recommendation"}:
            errors["epistemic_status"] = "정보 성격을 다시 선택해 주세요."
        review_status = values.get("review_status", "pending")
        if review_status not in {"pending", "approved"}:
            errors["review_status"] = "검토 상태를 다시 선택해 주세요."
        if review_status == "approved" and epistemic in {"inference", "recommendation"} and not raw_text:
            errors["raw_text"] = "계산·추정·AI 제안을 확정하려면 확인한 원문을 함께 남겨 주세요."
        if errors:
            return _site_detail_response(request, site_id, form_name="timeline", values=values, errors=errors)

        original = _store_optional_original(current_settings, raw_text, kind=f"timeline-{kind}")
        try:
            repo.create_event(
                site_id,
                kind,
                title,
                description=summary,
                occurred_at=occurred_at,
                time_precision="exact" if occurred_at else "unknown",
                epistemic_type=epistemic,
                review_status=review_status,
                evidence_ref=original.evidence_ref if original else None,
                actor_type="user",
            )
        except FieldBrainError as exc:
            _cleanup_uncommitted(original)
            return _site_detail_response(
                request, site_id, form_name="timeline", values=values, errors={},
                global_error=_friendly_core_error(exc),
            )
        return RedirectResponse(f"/sites/{site_id}?notice=event_created#timeline", status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    async def review(request: Request) -> HTMLResponse:
        pending = repo.pending_reviews(workspace_id)
        context = _common_context(request, active_page="review")
        context.update(
            {
                "review_items": present_review_items(
                    pending, repo.list_sites(workspace_id), repo.list_evidence(workspace_id)
                ),
                "review_sites": repo.list_sites(workspace_id),
                "open_review_id": None,
                "open_edit_id": None,
            }
        )
        return _render(request, "review.html", context)

    @app.post("/review/{target_id}/schedule-correction", response_class=HTMLResponse)
    async def schedule_candidate_correct(request: Request, target_id: str) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        pending = repo.pending_reviews(workspace_id)
        candidate = next(
            (row for row in pending if row.get("target_type") == "schedule_item" and row.get("id") == target_id),
            None,
        )
        error: str | None = None
        if candidate is None:
            error = "현재 수정할 수 있는 일정 후보가 아닙니다. 화면을 새로 열어 주세요."
        try:
            title = _text(values.get("title"), maximum=200, required=True)
            summary = _text(values.get("summary"), maximum=3_000, required=True)
            address = _text(values.get("address_text"), maximum=1_000)
            reason = _text(values.get("reason"), maximum=2_000, required=True)
            start_time = values.get("start_time", "").strip()
            end_date = values.get("end_date", "").strip()
            end_time = values.get("end_time", "").strip()
            start_at = _schedule_form_datetime(values.get("start_date", ""), start_time)
            end_at = _schedule_form_datetime(end_date, end_time) if end_date else None
            if end_time and not end_date:
                raise ValueError("end_time_without_date")
            precision = "range" if end_date else "exact" if start_time else "date"
        except ValueError:
            title = summary = address = reason = ""
            start_at = end_at = None
            precision = "date"
            error = error or "제목·내용·날짜·수정 이유를 확인해 주세요. 시간은 선택 사항입니다."
        if error is None and candidate is not None:
            try:
                repo.update_pending_schedule_item(
                    target_id,
                    title=title,
                    start_at=start_at or "",
                    end_at=end_at,
                    time_precision=precision,
                    summary=summary,
                    address_text=address or None,
                    site_id=values.get("site_id", "").strip() or None,
                    reason=reason,
                    actor_type="user",
                )
            except FieldBrainError as exc:
                error = _friendly_core_error(exc)
        if error is not None:
            sites = repo.list_sites(workspace_id)
            context = _common_context(request, active_page="review")
            context.update({
                "review_items": present_review_items(pending, sites, repo.list_evidence(workspace_id)),
                "review_sites": sites,
                "open_review_id": None,
                "open_edit_id": target_id,
                "global_error": error,
            })
            return _render(request, "review.html", context, status_code=422)
        return RedirectResponse("/review?notice=schedule_candidate_updated", status_code=303)

    @app.post("/review/{target_id}/money-correction", response_class=HTMLResponse)
    async def money_candidate_correct(request: Request, target_id: str) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        pending = repo.pending_reviews(workspace_id)
        candidate = next(
            (row for row in pending if row.get("target_type") == "money_item" and row.get("id") == target_id),
            None,
        )
        error: str | None = None
        try:
            amount = _optional_amount(values.get("amount", ""))
            purpose = values.get("purpose", "")
            if purpose not in MONEY_PURPOSES:
                raise ValueError("purpose")
            note = _text(values.get("note"), maximum=3_000, required=True)
            reason = _text(values.get("reason"), maximum=2_000, required=True)
        except ValueError:
            amount = None
            purpose = "other"
            note = reason = ""
            error = "금액·분류·내용·수정 이유를 확인해 주세요. 금액을 모르면 비워 둘 수 있습니다."
        if candidate is None:
            error = "현재 수정할 수 있는 금액 후보가 아닙니다. 화면을 새로 열어 주세요."
        if error is None and candidate is not None:
            if purpose in INFLOW_PURPOSES:
                direction = "inflow"
            elif purpose in OUTFLOW_PURPOSES:
                direction = "outflow"
            elif purpose == "discount":
                direction = "neutral"
            else:
                direction = str(candidate.get("direction") or "outflow")
            try:
                repo.create_money_item(
                    str(candidate["site_id"]), str(candidate["lineage_key"]), note[:300], amount,
                    purpose, str(candidate.get("progression") or "confirmed"),
                    str(candidate.get("actualness") or "actual"), direction,
                    notes=note, occurred_at=candidate.get("occurred_at"),
                    epistemic_type="fact", review_status="approved",
                    evidence_ref=candidate.get("evidence_ref"),
                    supersedes_money_item_id=target_id, correction_reason=reason,
                    actor_type="user",
                )
            except FieldBrainError as exc:
                error = _friendly_core_error(exc)
        if error is not None:
            sites = repo.list_sites(workspace_id)
            context = _common_context(request, active_page="review")
            context.update({
                "review_items": present_review_items(pending, sites, repo.list_evidence(workspace_id)),
                "review_sites": sites, "open_review_id": None, "open_edit_id": target_id,
                "global_error": error,
            })
            return _render(request, "review.html", context, status_code=422)
        return RedirectResponse("/review?notice=money_candidate_updated", status_code=303)

    @app.post("/review/{target_id}", response_class=HTMLResponse)
    async def review_save(request: Request, target_id: str) -> HTMLResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        target_type = values.get("target_type", "")
        decision = values.get("decision", "")
        reason = values.get("reason", "").strip()
        pending = repo.pending_reviews(workspace_id)
        candidate = next(
            (
                item
                for item in pending
                if str(item.get("id")) == target_id and str(item.get("target_type")) == target_type
            ),
            None,
        )
        error: str | None = None
        if candidate is None:
            error = "현재 검토할 수 있는 기록이 아닙니다. 화면을 새로 열어 주세요."
        elif decision not in {"approved", "held", "rejected"}:
            error = "검토 결과를 다시 선택해 주세요."
        elif not reason or len(reason) > 2_000:
            error = "무엇을 확인했는지 판단 근거를 2,000자 이내로 입력해 주세요."
        if error is None:
            action = {"approved": "approve", "held": "hold", "rejected": "reject"}[decision]
            try:
                repo.review_target(
                    target_type,
                    target_id,
                    action,
                    reason=reason,
                    actor_type="user",
                )
            except FieldBrainError as exc:
                error = _friendly_core_error(exc)
        if error is not None:
            context = _common_context(request, active_page="review")
            context.update(
                {
                    "review_items": present_review_items(
                        pending, repo.list_sites(workspace_id), repo.list_evidence(workspace_id)
                    ),
                    "review_sites": repo.list_sites(workspace_id),
                    "open_review_id": target_id,
                    "open_edit_id": None,
                    "global_error": error,
                }
            )
            return _render(request, "review.html", context, status_code=422)
        return RedirectResponse("/review?notice=review_saved", status_code=303)

    @app.post("/backups")
    async def backup_create(request: Request) -> RedirectResponse:
        values = await _form_dict(request)
        _validate_csrf(request, values)
        try:
            create_backup(current_settings.db_path, current_settings.backups_dir)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(
                status_code=500,
                detail="백업을 만들지 못했습니다. 데이터 폴더의 남은 공간을 확인해 주세요.",
            ) from exc
        return RedirectResponse("/?notice=backup_created", status_code=303)

    return app
