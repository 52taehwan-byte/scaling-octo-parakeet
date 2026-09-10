import unittest
from field_brain.attendance import attendance_mentions
from field_brain.attendance import labor_forecast

class AttendanceTests(unittest.TestCase):
    def test_rate_date_and_work_units(self):
        rates = [dict(name='아마라', amount=150000, effective_on='2026-09-01', saved_at='1'),
                 dict(name='아마라', amount=180000, effective_on='2026-09-20', saved_at='2')]
        result = labor_forecast('아마라 반나절 일했음', '2026-09-10', rates)
        self.assertEqual(result[0]['estimate'], 75000)
        self.assertIsNone(labor_forecast('아마라 일했음', '2026-09-10', rates)[0]['estimate'])
        self.assertIsNone(labor_forecast('아마라 하루 일했음', '2026-08-10', rates)[0]['estimate'])
    def test_explicit_workers(self):
        self.assertEqual(attendance_mentions('오늘 아마라, 니마, 우즈벡 1명 일했음'), [dict(name='아마라', count=1), dict(name='니마', count=1), dict(name='우즈벡', count=1)])

    def test_planned_work_is_not_attendance(self):
        self.assertEqual(attendance_mentions('내일 아마라 작업했으면 좋겠어'), [])
