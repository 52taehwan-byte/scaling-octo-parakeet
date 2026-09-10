import unittest
from field_brain.briefing import recent_records, note_sections


class BriefingTests(unittest.TestCase):
    def test_same_content_on_different_days_remains(self):
        rows = [{"summary": "작업 완료", "occurred_at_iso": day} for day in ["2026-09-09", "2026-09-09", "2026-09-10"]]
        self.assertEqual(len(recent_records(rows)), 2)
        self.assertEqual(len(rows), 3)

    def test_section_stops_at_unrelated_site_notes(self):
        result = note_sections("오늘 한 일:\n폐기물 반출\n사장이 한 말:\n다른 현장 견적\n돈: 비용 미확인")
        self.assertEqual(result, [{"title": "기록한 작업", "lines": ["폐기물 반출"]}, {"title": "금액 메모", "lines": ["비용 미확인"]}])

    def test_free_text_is_not_invented_as_completed_work(self):
        self.assertEqual(note_sections("내일 끝날 수도 있지만 확실하지 않음"), [])
