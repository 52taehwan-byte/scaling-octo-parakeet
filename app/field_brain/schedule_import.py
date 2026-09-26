"""Deterministic, review-first extraction from Korean business chat exports.

This intentionally extracts only explicit structured schedule blocks. Ambiguous
conversation remains in the immutable source for a later model-assisted pass.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone, timedelta
import hashlib
from pathlib import Path
import re
from typing import Any

from .originals import store_text_original
from .repository import ConflictError, FieldBrainRepository


SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")
MESSAGE_START = re.compile(r"(?m)^\d{4}년 \d{1,2}월 \d{1,2}일 (?:오전|오후) \d{1,2}:\d{2}(?:,|$)")
FIELD_LINE = re.compile(
    r"^[\s●★■\-]*(담당자|주소|내용|철거범위|번호|연락처|철거\s*희망\s*일정|철거\s*일정|공사\s*일정|방문\s*일정|특이사항|메모|업체\s*실행비|실행비)\s*[:：]\s*(.*)$"
)
PHONE = re.compile(r"01[016789][ -]?\d{3,4}[ -]?\d{4}")
ACCOUNT = re.compile(r"\b\d{3,4}-\d{2,4}-\d{4,}\b")


@dataclass(frozen=True, slots=True)
class ScheduleCandidate:
    schedule_type: str
    title: str
    start_at: str
    end_at: str | None
    time_precision: str
    summary: str
    address: str | None
    contact: str | None
    excerpt: str
    confidence: float
    source_comparison: str
    trusted_fixed_visit: bool = False
    trusted_fixed_work: bool = False
    contractor_amount_text: str | None = None


@dataclass(frozen=True, slots=True)
class ImportResult:
    created: int
    skipped: int
    extracted: int
    source_id: str
    approved: int = 0
    ignored_past: int = 0
    promoted: int = 0
    schedule_items: tuple[dict[str, Any], ...] = ()

    @property
    def pending(self) -> int:
        return self.created - self.approved + self.promoted


def _redact(text: str) -> str:
    return ACCOUNT.sub("[계좌정보 가림]", PHONE.sub("[연락처 가림]", text))


def _identity_text(value: str | None) -> str:
    """Normalize only for duplicate comparison; never replace the preserved original."""
    return re.sub(r"[^0-9가-힣a-z]", "", (value or "").casefold())


def _same_schedule_identity(existing: dict[str, Any], candidate: ScheduleCandidate) -> bool:
    if existing.get("schedule_type") != candidate.schedule_type or existing.get("start_at") != candidate.start_at:
        return False
    existing_contact = _identity_text(existing.get("customer_contact"))
    candidate_contact = _identity_text(candidate.contact)
    if existing_contact and candidate_contact and existing_contact == candidate_contact:
        return True
    existing_address = _identity_text(existing.get("address_text"))
    candidate_address = _identity_text(candidate.address)
    if existing_address and candidate_address:
        return existing_address == candidate_address or (
            min(len(existing_address), len(candidate_address)) >= 12
            and (existing_address in candidate_address or candidate_address in existing_address)
        )
    return _identity_text(existing.get("title")) == _identity_text(candidate.title)


def _message_chunks(text: str) -> list[str]:
    starts = [match.start() for match in MESSAGE_START.finditer(text)]
    if not starts:
        return [text]
    chunks = []
    if starts[0] > 0:
        chunks.append(text[: starts[0]])
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        chunks.append(text[start:end])
    return chunks


def _fields(chunk: str) -> dict[str, str]:
    result: dict[str, list[str]] = {}
    current: str | None = None
    for raw in chunk.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = FIELD_LINE.match(line)
        if match:
            current = re.sub(r"\s+", " ", match.group(1)).strip()
            current = {
                "방문일정": "방문 일정", "철거일정": "철거 일정",
                "공사일정": "공사 일정", "철거희망일정": "철거 희망 일정",
                "업체실행비": "업체 실행비",
            }.get(current.replace(" ", ""), current)
            result.setdefault(current, []).append(match.group(2).strip())
        elif current in {"내용", "철거범위", "특이사항", "메모"} and not MESSAGE_START.match(line):
            result[current].append(line)
    return {key: " ".join(part for part in parts if part).strip() for key, parts in result.items()}


def _korean_datetime(
    value: str, *, default_year: int, reference_date: date | None = None,
) -> tuple[str, str | None, str] | None:
    if not value or any(word in value for word in ("희망", "협의", "미정", "최대한")):
        return None
    # Expand a same-month range before matching dates (not time ranges).
    value = re.sub(r'(\d{1,2})월\s*(\d{1,2})일\s*[-~～–]\s*(\d{1,2})일',
                   lambda m: f'{m[1]}월 {m[2]}일 - {m[1]}월 {m[3]}일', value)
    dates = list(re.finditer(r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일", value))
    if not dates:
        if reference_date is None:
            return None
        resolved: date | None = None
        if any(word in value for word in ("금일", "오늘")):
            resolved = reference_date
        elif "내일" in value:
            resolved = reference_date + timedelta(days=1)
        else:
            weekdays = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}
            weekday = next((number for word, number in weekdays.items() if word in value), None)
            if weekday is not None:
                days_ahead = (weekday - reference_date.weekday()) % 7
                if "차주" in value or "다음주" in value or "다음 주" in value:
                    days_ahead = 7 - reference_date.weekday() + weekday
                resolved = reference_date + timedelta(days=days_ahead)
        if resolved is None:
            return None
        synthetic = f"{resolved.year}년 {resolved.month}월 {resolved.day}일 {value}"
        return _korean_datetime(synthetic, default_year=resolved.year, reference_date=reference_date)

    def build(match: re.Match[str], time_text: str) -> tuple[datetime, bool]:
        year = int(match.group(1) or default_year)
        month, day = int(match.group(2)), int(match.group(3))
        tm = re.search(
            r"(오전|오후)?\s*(\d{1,2})(?:시|(?=\s*[-~]\s*\d{1,2}\s*시))(?:\s*(\d{1,2})분)?",
            time_text,
        )
        if not tm:
            return datetime(year, month, day, 0, 0, tzinfo=SEOUL), False
        hour, minute = int(tm.group(2)), int(tm.group(3) or 0)
        if tm.group(1) and not 1 <= hour <= 12:
            raise ValueError('invalid twelve-hour time')
        if not tm.group(3) and re.match(r'\s*반', time_text[tm.end():]):
            minute = 30
        if tm.group(1) == "오후" and hour < 12:
            hour += 12
        if tm.group(1) == "오전" and hour == 12:
            hour = 0
        return datetime(year, month, day, hour, minute, tzinfo=SEOUL), True

    first_end = dates[1].start() if len(dates) > 1 else len(value)
    try:
        start, has_time = build(dates[0], value[dates[0].end() : first_end])
        end: datetime | None = None
        if len(dates) > 2:
            return None
        if len(dates) > 1:
            end, _ = build(dates[1], value[dates[1].end() :])
            if end < start:
                return None
    except ValueError:
        return None
    precision = "range" if end else "exact" if has_time else "date"
    return start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes") if end else None, precision


def extract_kakao_schedule_candidates(text: str, markdown_text: str = "") -> list[ScheduleCandidate]:
    candidates: list[ScheduleCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    raw_chunks = []
    for message in _message_chunks(text):
        message_lines = message.splitlines()
        message_header = message_lines[0] if message_lines else ""
        trusted_sender_message = any(name in message_header for name in ("김송호", "이준희"))
        trusted_visit_message = trusted_sender_message and (
            bool(re.search(r"<\s*방문\s*일정\s*픽스입니다\s*>", message))
            or "■방문 일정" in message
        )
        trusted_work_message = trusted_sender_message and (
            bool(re.search(r"<\s*공사\s*일정\s*픽스입니다\s*>", message))
            or "■공사건" in message
        )
        parts = re.split(r"(?m)(?=^\s*\d+\)\s*담당자\s*[:：])", message)
        for part in parts:
            if not part.strip():
                continue
            if (trusted_visit_message or trusted_work_message) and not part.startswith(message_header):
                markers = []
                if trusted_visit_message:
                    markers.append("<신뢰된 방문 일정 전달>")
                if trusted_work_message:
                    markers.append("<신뢰된 공사 일정 전달>")
                part = f"{message_header}\n{' '.join(markers)}\n{part}"
            raw_chunks.append(part)
    for chunk in raw_chunks:
        fields = _fields(chunk)
        address = fields.get("주소")
        summary = fields.get("내용") or fields.get("철거범위")
        contact = fields.get("연락처") or fields.get("번호")
        if not address or not summary:
            continue
        message_date_match = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", chunk)
        reference_date = (
            date(int(message_date_match.group(1)), int(message_date_match.group(2)), int(message_date_match.group(3)))
            if message_date_match else None
        )
        year_match = re.search(r"(\d{4})년", chunk)
        default_year = int(year_match.group(1)) if year_match else 2026
        first_line = chunk.splitlines()[0] if chunk.splitlines() else ""
        trusted_sender = any(name in first_line for name in ("김송호", "이준희"))
        fixed_visit_message = trusted_sender and (
            bool(re.search(r"<\s*방문\s*일정\s*픽스입니다\s*>", chunk))
            or "■방문 일정" in chunk
            or "<신뢰된 방문 일정 전달>" in chunk
        )
        fixed_work_message = trusted_sender and (
            bool(re.search(r"<\s*공사\s*일정\s*픽스입니다\s*>", chunk))
            or "■공사건" in chunk
            or "<신뢰된 공사 일정 전달>" in chunk
        )
        schedules: list[tuple[str, str]] = []
        if fields.get("공사 일정"):
            schedules.append(("work", fields["공사 일정"]))
        elif fields.get("철거 일정") and "희망" not in fields["철거 일정"]:
            schedules.append(("work", fields["철거 일정"]))
        if fields.get("방문 일정"):
            schedules.append(("estimate_visit", fields["방문 일정"]))
        for kind, raw_date in schedules:
            parsed = _korean_datetime(raw_date, default_year=default_year, reference_date=reference_date)
            if parsed is None:
                continue
            start, end, precision = parsed
            title = _title_from_address(address, summary)
            normalized_address = re.sub(r"\s+", "", address).replace(",", "")
            key = (kind, normalized_address, start)
            if key in seen:
                continue
            seen.add(key)
            explicit = any(marker in chunk for marker in ("확정", "픽스", "■공사건", "■공사 일정", "■방문 일정"))
            markdown_match = bool(markdown_text and (title in markdown_text or address in markdown_text))
            comparison = "matched" if markdown_match else "kakao_only"
            confidence = 0.97 if explicit and markdown_match else 0.93 if explicit else 0.82
            excerpt_lines = [f"주소: {address}", f"내용: {summary}", f"{'공사' if kind == 'work' else '방문'} 일정: {raw_date}"]
            contractor_amount = fields.get('업체 실행비') or fields.get('실행비')
            if kind != 'work' or not fixed_work_message:
                contractor_amount = None
            if contractor_amount:
                contractor_amount = _redact(contractor_amount)
                excerpt_lines.append(f'업체 실행비: {contractor_amount}')
            candidates.append(
                ScheduleCandidate(
                    kind, title, start, end, precision, summary, address, contact,
                    _redact("\n".join(excerpt_lines)), confidence, comparison,
                    trusted_fixed_visit=(kind == "estimate_visit" and fixed_visit_message),
                    trusted_fixed_work=(kind == "work" and fixed_work_message),
                    contractor_amount_text=contractor_amount,
                )
            )
    return candidates


def _title_from_address(address: str, summary: str) -> str:
    clean = re.sub(r"\s+", " ", address).strip()
    tokens = clean.split()
    location_words = {"서울", "경기", "경기도", "인천", "인천광역시", "서울특별시"}
    excluded = re.compile(r"^(?:\d+|\d+호|\d+층|\d+동|\d+~\d+층|본관|옥상)$")
    named = [token.strip(",") for token in tokens if re.search(r"[가-힣A-Za-z]", token) and not excluded.match(token.strip(","))]
    named = [token for token in named if token not in location_words and not token.endswith(("시", "구")) and not re.search(r"(?:로|길)\d", token)]
    if named:
        last = named[-1]
        if last.endswith(("점", "카페", "모바일", "연남")) and len(named) >= 2:
            previous = named[-2]
            if not previous.endswith(("동", "시", "구", "로", "길")):
                return f"{previous} {last}"
        if any(word in last for word in ("아파트", "캐슬", "에코", "아이아이", "이든", "아이쥬")):
            return last
    region = next((token.rstrip("시구") for token in tokens if token.endswith(("시", "구")) and len(token) > 2), "현장")
    if "헬스" in summary:
        return f"{region} 헬스장"
    if "소나무" in summary or "화분" in summary:
        return f"{region} 옥상 소나무 화분"
    if "집안" in summary or "몰딩" in summary or "싱크대" in summary:
        return f"{region} 아파트 부분 철거"
    return f"{region} 철거 현장"


def _source_for_text(
    repo: FieldBrainRepository,
    workspace_id: str,
    originals_dir: Path,
    text: str,
    original_name: str,
    source_type: str,
) -> dict[str, Any]:
    if len(text.encode('utf-8')) > 2_000_000:
        raise ValueError('각 일정 자료는 UTF-8 기준 2MB 이하여야 합니다.')
    digest = hashlib.sha256((text.strip() + "\n").encode("utf-8")).hexdigest()
    existing = next((row for row in repo.list_sources(workspace_id) if row["content_hash_sha256"] == digest), None)
    if existing:
        return existing
    stored = store_text_original(originals_dir, text, kind="schedule-source", max_characters=2_000_000)
    try:
        return repo.create_source(
            workspace_id, source_type, stored.sha256, f"local-original:{stored.relative_path}",
            original_name=original_name, mime_type="text/plain; charset=utf-8",
            byte_size=len(text.encode("utf-8")), processing_status="processed",
            privacy_level="sensitive", actor_type="user",
        )
    except Exception:
        stored.path.unlink(missing_ok=True)
        raise


def extract_markdown_schedule_candidates(text: str) -> list[ScheduleCandidate]:
    """Recognize dated field blocks only; never invent sender trust for a document."""
    results = []
    for block in re.split(r'(?m)^\s*(?:#{1,6}\s+.*|---+)\s*$', text):
        # No implicit current year for old documents or relative dates.
        if not re.search(r'\d{4}년\s*\d{1,2}월\s*\d{1,2}일', block):
            continue
        normalized = block.replace('**', '')
        if len(re.findall(r'(?m)^\s*[-●■]*\s*주소\s*[:：]', normalized)) != 1:
            continue  # Never merge multiple sites inside an unstructured section.
        try:
            candidates = extract_kakao_schedule_candidates(normalized)
        except ValueError:
            continue
        for candidate in candidates:
            results.append(replace(candidate, source_comparison='markdown_only',
                                   trusted_fixed_visit=False, trusted_fixed_work=False, contractor_amount_text=None))
    return results


def import_schedule_candidates(
    repo: FieldBrainRepository,
    workspace_id: str,
    originals_dir: Path,
    kakao_text: str,
    markdown_text: str,
    *,
    kakao_name: str = "KakaoTalkChats.txt",
    markdown_name: str = "Field-Brain.md",
    not_before: date | None = None,
) -> ImportResult:
    if not kakao_text.strip() and not markdown_text.strip():
        raise ValueError('일정 자료가 비어 있습니다.')
    kakao_source = None
    markdown_source = None
    sourced_candidates = []
    if kakao_text.strip():
        kakao_source = _source_for_text(repo, workspace_id, originals_dir, kakao_text, kakao_name, "message_export")
        sourced_candidates.extend((candidate, kakao_source) for candidate in extract_kakao_schedule_candidates(kakao_text, markdown_text))
    if markdown_text.strip():
        markdown_source = _source_for_text(repo, workspace_id, originals_dir, markdown_text, markdown_name, "document")
        sourced_candidates.extend((candidate, markdown_source) for candidate in extract_markdown_schedule_candidates(markdown_text))
    all_candidates = [candidate for candidate, _ in sourced_candidates]
    threshold = not_before or datetime.now(SEOUL).date()
    candidates = [
        (row, source) for row, source in sourced_candidates
        if row.trusted_fixed_visit or row.trusted_fixed_work
        or datetime.fromisoformat(row.start_at).date() >= threshold
    ]
    ignored_past = len(all_candidates) - len(candidates)
    existing_sites = repo.list_sites(workspace_id)
    existing_schedules = repo.list_schedule_items(workspace_id)
    created = skipped = approved = promoted = 0
    result_ids: dict[str, None] = {}
    for candidate, candidate_source in candidates:
        duplicate_row = next(
            (row for row in existing_schedules if _same_schedule_identity(row, candidate)), None
        )
        if duplicate_row is not None:
            result_ids[str(duplicate_row['id'])] = None
            if candidate.contractor_amount_text:
                repo.append_schedule_amount_note(str(duplicate_row['id']), candidate.contractor_amount_text, source_id=str(candidate_source['id']))
            if candidate.contact and not duplicate_row.get("customer_contact"):
                repo.enrich_schedule_contact(
                    str(duplicate_row["id"]), candidate.contact,
                    reason="카카오톡 픽스 원문의 연락처를 일정에 연결",
                    actor_type="system",
                )
                duplicate_row["customer_contact"] = candidate.contact
            if (candidate.trusted_fixed_visit or candidate.trusted_fixed_work) and duplicate_row.get("review_status") in {"pending", "held"}:
                fixed_label = "방문 일정" if candidate.trusted_fixed_visit else "공사 일정"
                repo.review_target(
                    "schedule_item", str(duplicate_row["id"]), "approve",
                    reason=f"사용자 지정 규칙: 김송호·이준희 과장의 {fixed_label} 픽스 메시지",
                    actor_type="user",
                )
                duplicate_row["review_status"] = "approved"
                approved += 1
                promoted += 1
            skipped += 1
            if candidate.trusted_fixed_visit:
                duplicate_row['site_id'] = repo.connect_imported_visit_site(workspace_id, str(duplicate_row['id']))
            continue
        site_id = None
        if candidate.schedule_type == "work":
            matches = [row for row in existing_sites
                       if row.get('address_text') == candidate.address and row.get('name') == candidate.title
                       and row.get('scope_summary') == candidate.summary
                       and row.get('business_status') not in ('completed', 'settled', 'cancelled')]
            site = matches[0] if len(matches) == 1 else None
            if site is None:
                site = repo.create_site(
                    workspace_id, candidate.title, address_text=candidate.address,
                    business_status="lead", scope_summary=candidate.summary,
                    notes="카카오톡 일정 후보에서 만든 현장. 일정 승인 전 원문 확인 필요.",
                    actor_type="user",
                )
                existing_sites.append(site)
            site_id = str(site["id"])
        evidence = repo.create_evidence(
            candidate_source["id"], "whole_source", excerpt=candidate.excerpt, actor_type="user"
        )
        auto_approved = candidate.trusted_fixed_visit or candidate.trusted_fixed_work
        row = repo.create_schedule_item(
            workspace_id, candidate.schedule_type, candidate.title, candidate.start_at,
            candidate.summary, site_id=site_id, end_at=candidate.end_at,
            time_precision=candidate.time_precision, customer_name=candidate.title if candidate.schedule_type == "estimate_visit" else None,
            customer_contact=candidate.contact if candidate.schedule_type == "estimate_visit" else None,
            address_text=candidate.address,
            notes=f'공사 픽스 업체금액: {candidate.contractor_amount_text}' if candidate.contractor_amount_text else None,
            epistemic_type="decision" if auto_approved else "claim",
            review_status="approved" if auto_approved else "pending",
            evidence_ref=f"evidence:{evidence['id']}", confidence=candidate.confidence,
            source_comparison=candidate.source_comparison,
            actor_type="user" if auto_approved else "ai",
        )
        existing_schedules.append(row)
        result_ids[str(row['id'])] = None
        if candidate.trusted_fixed_visit:
            row['site_id'] = repo.connect_imported_visit_site(workspace_id, str(row['id']))
            existing_sites = repo.list_sites(workspace_id)
        created += 1
        if auto_approved:
            approved += 1
    return ImportResult(
        created, skipped, len(all_candidates), str((kakao_source or markdown_source)["id"]), approved, ignored_past, promoted,
        tuple(sorted((repo.get_schedule_item(item_id) for item_id in result_ids),
                     key=lambda item: (item['start_at'], item['id'])))
    )
