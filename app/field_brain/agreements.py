"""Explicit local agreement input, not a general natural-language extractor."""
from datetime import datetime
from decimal import Decimal
import re


def parse_agreement(text: str) -> tuple[int, str | None]:
    pattern = (
        r"\s*(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<unit>만원|원)"
        r"\s*(?:에\s*)?(?:메이드|수주\s*확정|계약\s*확정)\s*[.!。]?\s*"
        r"(?:(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})\s*작업\s*시작\s*[.!。]?)?\s*"
    )
    match = re.fullmatch(pattern, text)
    if not match:
        raise ValueError('최종 금액과 확정된 소식만 적어 주세요. 예: 480만원에 메이드. 작업일이 정해졌다면 뒤에 2026-09-20 08:00 작업 시작을 붙여 주세요.')
    amount = Decimal(match['amount'].replace(',', '')) * (10000 if match['unit'] == '만원' else 1)
    if amount != amount.to_integral_value() or not 0 < amount <= 10**12:
        raise ValueError('최종 수주금액은 0원보다 큰 원 단위 금액으로 적어 주세요.')
    start = None
    if match['date']:
        try:
            start = datetime.fromisoformat(f"{match['date']}T{match['time']}+09:00").isoformat(timespec='minutes')
        except ValueError as exc:
            raise ValueError('작업 시작 날짜와 시간을 확인해 주세요.') from exc
    return int(amount), start
