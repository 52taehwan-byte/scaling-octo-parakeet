import unittest

from field_brain.capture import extract_candidates


class CaptureExtractionTests(unittest.TestCase):
    def test_expense_categories_and_customer_quote_direction(self):
        cases = [
            ("외주 인건비 실제 30만원", "outsource_cost", "outflow"),
            ("포크레인 작업자 비용 실제 80만원", "equipment_cost", "outflow"),
            ("방수재 실제 5만원", "material_cost", "outflow"),
            ("주유 실제 7만원", "transport_cost", "outflow"),
            ("폐기물 고객 청구 70만원", "base_quote", "inflow"),
        ]
        for text, purpose, direction in cases:
            with self.subTest(text=text):
                money = next(row for row in extract_candidates(text) if row.kind == "money_item")
                self.assertEqual(money.values["purpose"], purpose)
                self.assertEqual(money.values["direction"], direction)

    def test_one_note_becomes_event_task_money_and_risk_candidates(self):
        text = """오늘 화장실 철거를 완료함.
폐기물 처리비 실제 40만원을 지불함.
고객에게 토요일 잔금 일정을 확인해야 함.
고객이 작업 범위를 번복할 위험이 있어 문자 확정 필요."""
        candidates = extract_candidates(text)
        kinds = {item.kind for item in candidates}
        self.assertEqual(kinds, {"event", "task", "money_item", "risk"})
        money = next(item for item in candidates if item.kind == "money_item")
        self.assertEqual(money.values["amount_krw"], 400_000)
        self.assertEqual(money.values["purpose"], "waste_cost")
        self.assertEqual(money.values["actualness"], "actual")

    def test_amount_supports_won_and_decimal_manwon(self):
        rows = extract_candidates("장비 예상비 12.5만원\n운반비 180,000원")
        amounts = [row.values["amount_krw"] for row in rows if row.kind == "money_item"]
        self.assertEqual(amounts, [125_000, 180_000])

    def test_extractor_caps_candidates_and_keeps_original_event(self):
        text = "\n".join(f"확인해야 할 일 {index}" for index in range(30))
        candidates = extract_candidates(text)
        self.assertLessEqual(len(candidates), 20)
        self.assertEqual(candidates[0].kind, "event")


if __name__ == "__main__":
    unittest.main()
