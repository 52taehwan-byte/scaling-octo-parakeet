import unittest
from field_brain.direct_expenses import explicit_expenses

class DirectExpenseTests(unittest.TestCase):
    def test_separate_paid_amounts(self):
        rows = explicit_expenses('주유비 70,000원 지불, 점심 3만원 결제. 방수재 7.5만원 지급')
        self.assertEqual([r['amount'] for r in rows], [70000, 30000, 75000])
        self.assertEqual([r['purpose'] for r in rows], ['transport_cost', 'other', 'material_cost'])

    def test_uncertain_or_combined_amount_not_posted(self):
        for text in ['내일 인건비 30만원 지급 예정', '주유비 7만원 결제 안 함', '주유비 7만원 결제 취소', '인건비 30만원, 식비 3만원 총액 33만원 지급', '인건비 일당 15만원 2명 지급', '장비비 고객 청구 80만원 지급', '주유비 7만원 식비 3만원 결제']:
            with self.subTest(text=text):
                self.assertEqual(explicit_expenses(text), [])
