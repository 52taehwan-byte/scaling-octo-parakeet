from __future__ import annotations

import json
from datetime import date
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from field_brain.db import connect
from field_brain.presenter import build_dashboard_context
from field_brain.repository import ConflictError, FieldBrainRepository, ValidationError
from field_brain.schedule_import import extract_kakao_schedule_candidates, import_schedule_candidates


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "field-brain.sqlite3"
        self.repo = FieldBrainRepository(self.db_path)
        self.workspace = self.repo.create_workspace("test")
        self.site = self.repo.create_site(self.workspace["id"], "월곶 현장")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_visit_outcome_stays_with_site_and_does_not_become_revenue(self) -> None:
        from field_brain.presenter import build_site_context
        visit = self.repo.create_schedule_item(
            self.workspace['id'], 'estimate_visit', '가상상가',
            '2026-09-12T11:00+09:00', '가벽 철거', site_id=self.site['id'],
        )
        self.repo.save_estimate_visit_result(
            visit['id'], customer_requests='가벽 철거. 샷시는 보존.', total_quote_krw=5_000_000,
        )
        other = self.repo.create_site(self.workspace['id'], '다른 현장')
        self.assertEqual(self.repo.list_site_visit_results(other['id']), [])
        detail = build_site_context(self.repo, self.workspace['id'], self.site)
        self.assertEqual(detail['visit_results'][0]['total_quote_krw'], 5_000_000)
        self.assertIsNone(detail['financial']['agreed_customer_amount'])
        self.repo.save_estimate_visit_result(
            visit['id'], customer_requests='고객과 조정한 견적', total_quote_krw=4_800_000,
        )
        rows = self.repo.list_site_visit_results(self.site['id'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['total_quote_krw'], 4_800_000)
        self.assertEqual(rows[0]['revision'], 2)

    def test_work_and_estimate_visit_can_overlap(self) -> None:
        work = self.repo.create_schedule_item(
            self.workspace["id"], "work", "월곶 현장", "2026-09-03T08:00+09:00",
            "폐기물 반출", site_id=self.site["id"], end_at="2026-09-03T17:00+09:00",
        )
        visit = self.repo.create_schedule_item(
            self.workspace["id"], "estimate_visit", "약국 고객", "2026-09-03T10:00+09:00",
            "가벽과 간판 범위 확인", customer_name="약국 고객", address_text="시흥시",
        )
        rows = self.repo.list_schedule_items(self.workspace["id"])
        self.assertEqual([work["id"], visit["id"]], [row["id"] for row in rows])

    def test_work_requires_owned_site(self) -> None:
        with self.assertRaises(ValidationError):
            self.repo.create_schedule_item(
                self.workspace["id"], "work", "작업", "2026-09-03T08:00+09:00", "철거"
            )

    def test_end_cannot_be_before_start(self) -> None:
        with self.assertRaises(ValidationError):
            self.repo.create_schedule_item(
                self.workspace["id"], "work", "작업", "2026-09-03T17:00+09:00", "철거",
                site_id=self.site["id"], end_at="2026-09-03T08:00+09:00",
            )

    def test_contact_and_address_are_redacted_from_audit(self) -> None:
        item = self.repo.create_schedule_item(
            self.workspace["id"], "estimate_visit", "고객", "2026-09-03T10:00+09:00", "현장 확인",
            customer_contact="010-1234-5678", address_text="상세 주소",
        )
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT after_json FROM audit_events WHERE target_id = ?", (item["id"],)
            ).fetchone()
        audit = json.loads(row["after_json"])
        self.assertEqual(audit["customer_contact"], "[redacted]")
        self.assertEqual(audit["address_text"], "[redacted]")

    def test_structured_chat_extracts_confirmed_but_not_desired_work_date(self) -> None:
        text = """2026년 9월 1일 오후 2:29, 담당자 : 계약 확정되어 전달드립니다!\n
■공사건\n- 주소 : 서울 가상구 예시로 15 가상모바일\n- 내용 : 전체 철거\n- 공사 일정 : 9월 4일\n
2026년 9월 1일 오후 3:00, 담당자 : ■방문 일정\n
●주소 : 경기도 성남시 음식점\n●내용 : 내부 전체 철거\n●철거 일정 : 9월 15일 진행 희망\n●방문 일정 : 9월 3일 오전 11시\n"""
        rows = extract_kakao_schedule_candidates(text)
        self.assertEqual([(row.schedule_type, row.start_at) for row in rows], [
            ("work", "2026-09-04T00:00+09:00"),
            ("estimate_visit", "2026-09-03T11:00+09:00"),
        ])

    def test_import_is_review_first_and_idempotent(self) -> None:
        text = """2026년 9월 1일 오후 2:29, 담당자 : 계약 확정\n
■공사건\n- 주소 : 서울 가상구 예시로 15 가상모바일\n- 내용 : 전체 철거\n- 공사 일정 : 9월 4일\n"""
        originals = Path(self.temp.name) / "originals"
        first = import_schedule_candidates(
            self.repo, self.workspace["id"], originals, text, "", not_before=date(2026, 9, 1)
        )
        second = import_schedule_candidates(
            self.repo, self.workspace["id"], originals, text, "", not_before=date(2026, 9, 1)
        )
        self.assertEqual((first.created, second.skipped), (1, 1))
        pending = self.repo.pending_reviews(self.workspace["id"])
        schedule = next(row for row in pending if row["target_type"] == "schedule_item")
        self.assertEqual(schedule["review_status"], "pending")
        self.assertEqual(self.repo.list_schedule_items(self.workspace["id"], review_status="approved"), [])
        self.repo.review_target("schedule_item", schedule["id"], "approve", reason="카카오톡 확정 문구 확인")
        self.assertEqual(len(self.repo.list_schedule_items(self.workspace["id"], review_status="approved")), 1)

    def test_trusted_manager_fixed_visit_is_approved_even_when_historical(self) -> None:
        text = """2026년 8월 20일 오전 10:08, 김송호과장님 : <방문 일정 픽스입니다>

주소 : 서울특별시 가상구 예시로 86 가상상가
내용 : 120평 전체 철거 및 원상복구
방문 일정 : 8월 24일 오후 2시
"""
        result = import_schedule_candidates(
            self.repo, self.workspace["id"], Path(self.temp.name) / "fixed-originals", text, ""
        )
        self.assertEqual((result.created, result.approved), (1, 1))
        approved = self.repo.list_schedule_items(
            self.workspace["id"], schedule_type="estimate_visit", review_status="approved"
        )
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["start_at"], "2026-08-24T14:00+09:00")

    def test_untrusted_fixed_phrase_still_requires_review(self) -> None:
        text = """2026년 9월 2일 오전 10:08, 다른사람 : <방문 일정 픽스입니다>

주소 : 서울시 테스트구 테스트로 1
내용 : 내부 철거
방문 일정 : 9월 24일 오후 2시
"""
        result = import_schedule_candidates(
            self.repo, self.workspace["id"], Path(self.temp.name) / "untrusted-originals", text, ""
        )
        self.assertEqual(result.approved, 0)
        self.assertEqual(len(self.repo.list_schedule_items(self.workspace["id"], review_status="pending")), 1)

    def test_same_visit_is_not_duplicated_when_address_gains_store_name(self) -> None:
        first = """2026년 8월 20일 오후 5:00, 이준희 과장 청년철거 : <방문 일정 픽스입니다>

주소 : 경기도 예시시 가상구 테스트로 31, 1층
내용 : 약9평 전체 철거 후 원상복구
연락처 : 010-0000-0000
방문 일정 : 8월 21일 오전 10시
"""
        repeated = """2026년 8월 21일 오전 7:00, 이준희 과장 청년철거 : <방문 일정 픽스입니다>

주소 : 경기도 예시시 가상구 테스트로 31, 1층 가상식당 예시점
내용 : 약9평 전체 철거 후 원상복구
연락처 : 010-0000-0000
방문 일정 : 8월 21일 오전 10시
"""
        originals = Path(self.temp.name) / "same-visit"
        first_result = import_schedule_candidates(self.repo, self.workspace["id"], originals, first, "")
        repeated_result = import_schedule_candidates(self.repo, self.workspace["id"], originals, repeated, "")
        self.assertEqual((first_result.created, repeated_result.created, repeated_result.skipped), (1, 0, 1))
        visits = self.repo.list_schedule_items(
            self.workspace["id"], schedule_type="estimate_visit", review_status="approved"
        )
        self.assertEqual(len(visits), 1)

    def test_trusted_manager_fixed_work_goes_to_work_schedule_not_visit(self) -> None:
        text = """2026년 9월 2일 오전 9:00, 이준희 과장 청년철거 : <공사 일정 픽스입니다>

주소 : 경기도 화성시 테스트로 1 아이쥬크림
내용 : 간판 및 가벽 철거
공사 일정 : 9월 3일 오전 9시
"""
        result = import_schedule_candidates(
            self.repo, self.workspace["id"], Path(self.temp.name) / "fixed-work", text, ""
        )
        self.assertEqual(result.approved, 1)
        work = self.repo.list_schedule_items(
            self.workspace["id"], schedule_type="work", review_status="approved"
        )
        visits = self.repo.list_schedule_items(
            self.workspace["id"], schedule_type="estimate_visit", review_status="approved"
        )
        self.assertEqual(len(work), 1)
        self.assertEqual(visits, [])

    def test_pending_candidate_can_be_corrected_and_relinked_with_audit(self) -> None:
        temporary = self.repo.create_site(
            self.workspace["id"], "자동 생성 현장", notes="카카오톡 일정 후보에서 만든 현장"
        )
        existing = self.repo.create_site(self.workspace["id"], "실제 등록 현장")
        candidate = self.repo.create_schedule_item(
            self.workspace["id"], "work", "틀린 이름", "2026-09-03T00:00+09:00", "전체 철거",
            site_id=temporary["id"], time_precision="date", review_status="pending", actor_type="ai",
            evidence_ref="evidence:kept",
        )
        updated = self.repo.update_pending_schedule_item(
            candidate["id"], title="바른 이름", start_at="2026-09-04T09:30+09:00", end_at=None,
            time_precision="exact", summary="부분 철거", address_text="확인한 주소",
            site_id=existing["id"], reason="원문 재확인",
        )
        self.assertEqual((updated["title"], updated["site_id"], updated["evidence_ref"], updated["revision"]),
                         ("바른 이름", existing["id"], "evidence:kept", 2))
        with closing(connect(self.db_path)) as connection:
            audit = connection.execute(
                "SELECT action_type, reason FROM audit_events WHERE target_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
                (candidate["id"],),
            ).fetchone()
        self.assertEqual((audit["action_type"], audit["reason"]), ("correct_candidate", "원문 재확인"))
        visible_names = [row["name"] for row in build_dashboard_context(self.repo, self.workspace["id"])["sites"]]
        self.assertNotIn("자동 생성 현장", visible_names)
        self.assertIn("실제 등록 현장", visible_names)

    def test_approved_schedule_cannot_be_corrected(self) -> None:
        schedule = self.repo.create_schedule_item(
            self.workspace["id"], "work", "확정 일정", "2026-09-03T08:00+09:00", "철거",
            site_id=self.site["id"], review_status="approved",
        )
        with self.assertRaises(ConflictError):
            self.repo.update_pending_schedule_item(
                schedule["id"], title="변경", start_at="2026-09-04T08:00+09:00", end_at=None,
                time_precision="exact", summary="변경", address_text=None,
                site_id=self.site["id"], reason="바꾸기",
            )

    def test_reschedule_and_cancel_preserve_audit_history(self) -> None:
        visit = self.repo.create_schedule_item(
            self.workspace["id"], "estimate_visit", "약국", "2026-08-27T12:00+09:00",
            "철거 견적", customer_contact="010-1111-2222", address_text="한강자이타워",
        )
        changed = self.repo.reschedule_item(
            visit["id"], start_at="2026-08-28T12:00+09:00", end_at=None,
            time_precision="exact", reason="후속 일일 일정에서 28일로 확인",
        )
        self.assertEqual(changed["start_at"], "2026-08-28T12:00+09:00")
        self.assertEqual(changed["source_comparison"], "later_confirmation")
        cancelled = self.repo.set_schedule_cancelled(
            visit["id"], cancelled=True, reason="고객이 방문 취소 요청",
        )
        self.assertEqual(cancelled["business_status"], "cancelled")
        restored = self.repo.set_schedule_cancelled(
            visit["id"], cancelled=False, reason="고객이 방문을 다시 요청",
        )
        self.assertEqual(restored["business_status"], "scheduled")
        with closing(connect(self.db_path)) as connection:
            history = connection.execute(
                "SELECT action_type, reason, before_json, after_json FROM audit_events WHERE target_id = ? ORDER BY rowid",
                (visit["id"],),
            ).fetchall()
        self.assertEqual([row["action_type"] for row in history[-3:]], [
            "reschedule", "cancel_schedule", "restore_schedule"
        ])
        before = json.loads(history[-3]["before_json"])
        after = json.loads(history[-3]["after_json"])
        self.assertEqual(before["start_at"], "2026-08-27T12:00+09:00")
        self.assertEqual(after["start_at"], "2026-08-28T12:00+09:00")

    def test_estimate_visit_result_keeps_unknown_costs_null_and_can_be_updated(self) -> None:
        visit = self.repo.create_schedule_item(
            self.workspace["id"], "estimate_visit", "화성 식당", "2026-09-18T15:00+09:00",
            "42평 전체 철거 견적", address_text="화성시",
        )
        first = self.repo.save_estimate_visit_result(
            visit["id"], customer_requests="가벽·바닥·간판 철거",
            work_plan="1일차 기공2 조공2", labor_cost_krw=3_650_000,
            equipment_plan="사다리차·포크레인", equipment_cost_krw=2_450_000,
            waste_plan="통합폐기물 3차", waste_cost_krw=4_100_000,
            restoration_plan="샷시 복원", restoration_cost_krw=3_100_000,
            total_quote_krw=13_300_000, conditions_text="필름 미제거 시 50만원 감소",
        )
        self.assertEqual(first["total_quote_krw"], 13_300_000)
        updated = self.repo.save_estimate_visit_result(
            visit["id"], customer_requests="가벽·바닥·간판 철거", work_plan="일정 재산정",
            labor_cost_krw=None, total_quote_krw=None,
        )
        self.assertIsNone(updated["labor_cost_krw"])
        self.assertIsNone(updated["total_quote_krw"])
        self.assertEqual(updated["revision"], 2)


if __name__ == "__main__":
    unittest.main()
