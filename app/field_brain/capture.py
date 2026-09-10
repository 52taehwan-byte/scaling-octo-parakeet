"""Local-first integrated record capture and conservative candidate extraction.

The extractor is deliberately simple.  It proves the product flow before an
external model is connected: preserve once, propose many, confirm by a person.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


AMOUNT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(만원|원)")
TASK_WORDS = ("해야", "예정", "필요", "확인", "추후", "내일", "토요일", "마무리", "남은 일")
RISK_WORDS = ("위험", "안전", "슬리퍼", "번복", "악성", "미수", "분쟁", "불확실", "모름", "추가 작업")


@dataclass(frozen=True, slots=True)
class Candidate:
    kind: str
    title: str
    values: dict[str, Any]
    excerpt: str


def _lines(text: str) -> list[str]:
    rows: list[str] = []
    for raw in text.splitlines():
        clean = re.sub(r"^[\s\-–—*•·>\d.)]+", "", raw).strip()
        if clean and clean not in rows:
            rows.append(clean)
    return rows


def _amount_krw(line: str) -> int | None:
    matches = list(AMOUNT_RE.finditer(line.replace(",", "")))
    if not matches:
        return None
    match = matches[-1]
    value = float(match.group(1))
    return int(round(value * 10_000 if match.group(2) == "만원" else value))


def _money_fields(line: str) -> tuple[str, str, str, str]:
    if any(word in line for word in ("고물 판매", "고철 판매", "매각 수익")):
        purpose = "scrap_income"
    elif any(word in line for word in ("견적", "청구")):
        purpose = "base_quote"
    elif "외주" in line:
        purpose = "outsource_cost"
    elif any(word in line for word in ("포크레인", "굴삭기", "스카이차", "사다리차", "장비")):
        purpose = "equipment_cost"
    elif any(word in line for word in ("인건비", "기공", "조공", "작업자")):
        purpose = "labor_cost"
    elif any(word in line for word in ("포크레인", "장비")):
        purpose = "equipment_cost"
    elif any(word in line for word in ("폐기물", "고물상", "처리비")):
        purpose = "waste_cost"
    elif any(word in line for word in ("외주", "샷시", "도색")):
        purpose = "outsource_cost"
    elif any(word in line for word in ("자재", "소모품", "방수재", "시멘트", "실리콘", "절단날")):
        purpose = "material_cost"
    elif any(word in line for word in ("주유", "유류", "통행료", "주차", "운반비")):
        purpose = "transport_cost"
    elif "수수료" in line:
        purpose = "commission"
    elif any(word in line for word in ("고물 판매", "고철 판매", "매각 수익")):
        purpose = "scrap_income"
    elif any(word in line for word in ("잔금", "입금", "받음", "수익")):
        purpose = "settlement"
    elif any(word in line for word in ("견적", "청구")):
        purpose = "base_quote"
    else:
        purpose = "other"

    inflow = purpose in {"base_quote", "scrap_income", "settlement"}
    direction = "inflow" if inflow else "outflow"
    actualness = "actual" if any(word in line for word in ("실제", "지불", "받음", "처리함", "팔고")) else "estimated"
    progression = "confirmed" if actualness == "actual" else "candidate"
    return purpose, direction, actualness, progression


def _risk_fields(line: str) -> tuple[str, str]:
    if any(word in line for word in ("안전", "슬리퍼", "보호구", "사고")):
        return "safety", "high"
    if any(word in line for word in ("번복", "추가 작업", "범위", "분쟁", "악성")):
        return "scope", "high"
    if "미수" in line:
        return "cost", "high"
    if any(word in line for word in ("일정", "늦", "지연")):
        return "schedule", "medium"
    return "other", "medium"


def extract_candidates(text: str) -> list[Candidate]:
    """Extract conservative pending candidates from one Korean field note."""
    rows = _lines(text)
    if not rows:
        return []
    candidates: list[Candidate] = [
        Candidate(
            "event",
            rows[0][:120],
            {"event_type": "note", "description": text.strip(), "epistemic_type": "claim"},
            text.strip()[:2_000],
        )
    ]
    seen: set[tuple[str, str]] = {("event", rows[0][:120])}
    for line in rows:
        amount = _amount_krw(line)
        if amount is not None:
            purpose, direction, actualness, progression = _money_fields(line)
            item = Candidate(
                "money_item",
                line[:120],
                {
                    "amount_krw": amount,
                    "purpose": purpose,
                    "direction": direction,
                    "actualness": actualness,
                    "progression": progression,
                    "epistemic_type": "claim",
                },
                line,
            )
            if (item.kind, item.title) not in seen:
                candidates.append(item)
                seen.add((item.kind, item.title))
        if any(word in line for word in RISK_WORDS):
            category, severity = _risk_fields(line)
            item = Candidate(
                "risk",
                line[:120],
                {"category": category, "severity": severity, "epistemic_type": "inference"},
                line,
            )
            if (item.kind, item.title) not in seen:
                candidates.append(item)
                seen.add((item.kind, item.title))
        if any(word in line for word in TASK_WORDS) and not line.endswith(("했다", "했음", "완료")):
            item = Candidate(
                "task",
                line[:120],
                {"phase": "other", "priority": "normal", "epistemic_type": "inference"},
                line,
            )
            if (item.kind, item.title) not in seen:
                candidates.append(item)
                seen.add((item.kind, item.title))
    return candidates[:20]
