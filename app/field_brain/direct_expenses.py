"""Parse explicit user-entered expense statements, without model inference."""
import re
from decimal import Decimal

AMOUNT = re.compile(r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(만원|원)")
BLOCK = re.compile(r"예상|예정|견적|청구|내일|모레|어제|지난|미지급|미결제|않|안\s|못\s|환불|취소|합계|총액|각각|인당|일당|평당|추가|[?？×=]|\d\s*[xX]|이라고|라고|했대|다른\s*현장")
PAID = re.compile(r"지불|지출|결제|지급|송금|썼|사용했")
CATEGORIES = [
    ("outsource_cost", ("외주",)),
    ("equipment_cost", ("포크레인", "스카이차", "사다리차", "굴삭기", "장비비")),
    ("labor_cost", ("인건비",)),
    ("waste_cost", ("폐기비", "폐기물", "왈가닥")),
    ("material_cost", ("자재", "소모품", "방수재", "시멘트", "실리콘", "절단날")),
    ("transport_cost", ("주유", "유류", "통행료", "주차비", "운반비")),
    ("other", ("식비", "점심", "저녁", "음료", "커피", "생수")),
]


def explicit_expenses(text):
    rows = []
    # Do not split commas in 70,000 or decimal points in 7.5만원.
    for part in re.split(r"\n|;|(?<!\d),|,(?!\d)|\.(?!\d)", text):
        part = part.strip()
        if not PAID.search(part) or BLOCK.search(part) or "수수료" in part:
            continue
        amounts = list(AMOUNT.finditer(part))
        if len(amounts) != 1:
            continue
        matched = [(purpose, words) for purpose, words in CATEGORIES if any(word in part for word in words)]
        # Outsourced labour and equipment operators have a single inclusive price.
        if matched and matched[0][0] in {"outsource_cost", "equipment_cost"}:
            matched = matched[:1]
        if len(matched) != 1:
            continue
        amount = Decimal(amounts[0][1].replace(",", "")) * (10000 if amounts[0][2] == "만원" else 1)
        if amount <= 0 or amount != int(amount):
            continue
        rows.append({"text": part, "purpose": matched[0][0], "amount": int(amount)})
    return rows
