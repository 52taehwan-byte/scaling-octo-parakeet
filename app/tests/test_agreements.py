import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

from field_brain.agreements import parse_agreement
from field_brain.db import connect
from field_brain.repository import FieldBrainRepository, ValidationError, NotFoundError
from field_brain.presenter import build_site_context


class AgreementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = FieldBrainRepository(Path(self.temp.name) / 'test.db')
        self.ws = self.repo.create_workspace('test')['id']
        self.site = self.repo.create_site(self.ws, '가상상가', business_status='estimating')
        self.visit = self.repo.create_schedule_item(self.ws, 'estimate_visit', '가상상가',
            '2026-09-12T11:00+09:00', '가벽 철거', site_id=self.site['id'])
        self.repo.save_estimate_visit_result(self.visit['id'], customer_requests='가벽 철거. 샷시 보존', total_quote_krw=5000000)

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, text='480만원에 메이드. 2026-09-20 08:00 작업 시작'):
        return self.repo.apply_visit_agreement(self.ws, self.visit['id'], text)

    def test_parser_rejects_ambiguous_or_negative_statements(self):
        for text in ('480만원 메이드 아님', '480만원 메이드 예정', '480만원 메이드?',
                     '480만원 메이드. 내일 시작', '500만원 제안 480만원 메이드',
                     '480만원 메이드. 2026-02-30 08:00 작업 시작', '0만원 메이드',
                     '480만원 메이드. 취소', '480만원 메이드. 추가 20만원', '0.1원 메이드'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_agreement(text)
        self.assertEqual(parse_agreement('4,800,000원 계약 확정'), (4800000, None))

    def test_proposal_agreement_work_and_correction_share_site(self):
        self.apply()
        self.apply()
        self.assertEqual(len(self.repo.list_schedule_items(self.ws, schedule_type='work')), 1)
        self.assertEqual(len(self.repo.list_for_site(self.site['id'], 'money_item')), 1)
        self.assertEqual(self.repo.get_estimate_visit_result(self.visit['id'])['total_quote_krw'], 5000000)
        self.assertEqual(self.repo.get_site(self.site['id'])['business_status'], 'scheduled')
        self.apply('470만원에 메이드')
        self.apply()  # An old resubmission cannot restore the old agreed amount.
        detail = build_site_context(self.repo, self.ws, self.repo.get_site(self.site['id']))
        self.assertEqual(detail['financial']['agreed_customer_amount'], 4700000)
        self.assertEqual(len(self.repo.list_for_site(self.site['id'], 'money_item')), 2)
        work = self.repo.list_schedule_items(self.ws, schedule_type='work')[0]
        self.assertEqual(work['summary'], '가벽 철거. 샷시 보존')
        self.assertEqual(work['site_id'], self.site['id'])

    def test_amount_only_then_start_date(self):
        self.apply('480만원 메이드')
        self.assertEqual(self.repo.list_schedule_items(self.ws, schedule_type='work'), [])
        self.apply()
        self.assertEqual(len(self.repo.list_for_site(self.site['id'], 'money_item')), 1)
        self.assertEqual(len(self.repo.list_schedule_items(self.ws, schedule_type='work')), 1)

    def test_failed_write_rolls_back_all_changes(self):
        original = self.repo._audit
        def fail_schedule(connection, **kwargs):
            if kwargs['target_type'] == 'schedule_item':
                raise RuntimeError('simulated failure')
            return original(connection, **kwargs)
        with patch.object(self.repo, '_audit', side_effect=fail_schedule), self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(self.repo.list_for_site(self.site['id'], 'money_item'), [])
        self.assertEqual(self.repo.list_schedule_items(self.ws, schedule_type='work'), [])
        self.assertEqual(self.repo.get_site(self.site['id'])['business_status'], 'estimating')

    def test_existing_quote_is_superseded_not_added(self):
        self.repo.create_money_item(self.site['id'], 'old-quote', '제안 견적', 5000000,
                                    'base_quote', 'offered', 'estimated', 'inflow')
        self.apply()
        detail = build_site_context(self.repo, self.ws, self.repo.get_site(self.site['id']))
        self.assertEqual(detail['financial']['agreed_customer_amount'], 4800000)

    def test_date_change_does_not_partially_change_amount(self):
        self.apply()
        with self.assertRaises(ValidationError):
            self.apply('460만원 메이드. 2026-09-21 08:00 작업 시작')
        self.assertEqual(len(self.repo.list_for_site(self.site['id'], 'money_item')), 1)

    def test_foreign_workspace_is_rejected(self):
        other = self.repo.create_workspace('other')['id']
        with self.assertRaises(NotFoundError):
            self.repo.apply_visit_agreement(other, self.visit['id'], '480만원 메이드')

    def test_unlinked_and_cancelled_visits_are_rejected(self):
        visit = self.repo.create_schedule_item(self.ws, 'estimate_visit', '미연결', '2026-09-12', '방문')
        with self.assertRaises(ValidationError):
            self.repo.apply_visit_agreement(self.ws, visit['id'], '480만원 메이드')
        self.repo.set_schedule_cancelled(self.visit['id'], cancelled=True, reason='취소')
        with self.assertRaises(ValidationError):
            self.apply()

    def test_original_note_survives_in_audit_and_money(self):
        self.apply()
        with closing(connect(self.repo.db_path)) as connection:
            note = connection.execute("SELECT notes FROM money_items WHERE site_id=?", (self.site['id'],)).fetchone()[0]
            self.assertIn('480만원에 메이드', note)
            self.assertGreater(connection.execute("SELECT count(*) FROM audit_events WHERE target_type='money_item'").fetchone()[0], 0)
