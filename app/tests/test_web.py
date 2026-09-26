from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

try:
    from field_brain.config import Settings
    from field_brain.main import create_app

    HAS_WEB_DEPENDENCIES = True
except ModuleNotFoundError:
    HAS_WEB_DEPENDENCIES = False


async def asgi_request(
    app,
    path: str,
    *,
    method: str = "GET",
    form: dict[str, str] | None = None,
    host: str = "testserver",
):
    body = urlencode(form or {}).encode("utf-8")
    events = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        events.append(message)

    raw_path, _, raw_query = path.partition("?")
    headers = [(b"host", host.encode("ascii"))]
    if method == "POST":
        headers.extend(
            [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode("ascii")),
            ]
        )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": raw_query.encode("utf-8"),
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 54321),
        "server": (host, 80),
    }
    await app(scope, receive, send)
    start = next(event for event in events if event["type"] == "http.response.start")
    payload = b"".join(
        event.get("body", b"") for event in events if event["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin1").lower(): value.decode("latin1") for key, value in start["headers"]
    }
    return start["status"], response_headers, payload


async def asgi_multipart_request(app, path: str, *, fields: dict[str, str], files: dict[str, tuple[str, bytes]]):
    boundary = "----FieldBrainUploadBoundary"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    for name, (filename, content) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            'Content-Type: text/plain; charset=utf-8\r\n\r\n'.encode("utf-8") + content + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    body = b"".join(parts)
    events = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        events.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode("utf-8"), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver"),
                    (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
                    (b"content-length", str(len(body)).encode("ascii"))],
        "client": ("127.0.0.1", 54321), "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(event for event in events if event["type"] == "http.response.start")
    payload = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    return start["status"], payload


@unittest.skipUnless(HAS_WEB_DEPENDENCIES, "FastAPI web dependencies are not installed")
class WebSmokeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        settings = Settings(
            data_root=root,
            db_path=root / "demo.db",
            originals_dir=root / "originals",
            backups_dir=root / "backups",
            demo=True,
            host="127.0.0.1",
            port=8765,
        )
        self.app = create_app(settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, *args, **kwargs):
        return asyncio.run(asgi_request(self.app, *args, **kwargs))

    def csrf(self) -> str:
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', body)
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def test_schedule_import_accepts_both_selected_files(self) -> None:
        kakao = """2026년 9월 2일 오전 10:08, 김송호과장님 : <방문 일정 픽스입니다>

주소 : 서울특별시 가상구 예시로 86 가상상가
내용 : 120평 전체 철거 및 원상복구
방문 일정 : 9월 24일 오후 2시
""".encode("utf-8")
        markdown = "# 철거 업무 정리\n\n카카오톡 자료와 대조합니다.\n".encode("utf-8")
        status, body = asyncio.run(asgi_multipart_request(
            self.app, "/schedule-import", fields={"csrf_token": self.csrf()},
            files={
                "kakao_file": ("KakaoTalkChats.txt", kakao),
                "markdown_file": ("field-brain.md", markdown),
            },
        ))
        self.assertEqual(status, 200)
        self.assertNotIn("파일을 모두 선택".encode("utf-8"), body)
        decoded = body.decode("utf-8")
        self.assertIn("자료 처리 완료", decoded)
        self.assertIn("바로 등록됨", decoded)
        self.assertIn("정보가 부족한 일정", decoded)
        self.assertIn("작업 일정 보기", decoded)
        self.assertIn("방문 일정 보기", decoded)
        self.assertIn("이번 자료에 연결된 일정", decoded)
        self.assertIn("120평 전체 철거 및 원상복구", decoded)
        schedules = self.app.state.repo.list_schedule_items(self.app.state.workspace_id)
        imported = next(row for row in schedules if row['summary'] == '120평 전체 철거 및 원상복구')
        self.assertIn(f'/schedules/{imported["id"]}', decoded)
        self.assertIn(f'/sites/{imported["site_id"]}', decoded)
        status, repeated = asyncio.run(asgi_multipart_request(
            self.app, '/schedule-import', fields={'csrf_token': self.csrf()},
            files={'kakao_file': ('KakaoTalkChats.txt', kakao)}))
        self.assertEqual(status, 200)
        self.assertIn(f'/schedules/{imported["id"]}', repeated.decode('utf-8'))

    def test_schedule_import_single_files_and_input_errors(self) -> None:
        examples = {
            'kakao_file': ('chat.txt', '2099년 9월 2일 오전 10:08, 김송호과장님 : <방문 일정 픽스입니다>\n주소 : 가상시 테스트로 15\n내용 : 가벽 철거\n방문 일정 : 9월 24일 오후 2시'),
            'markdown_file': ('note.md', '# 현장\n주소 : 가상시 별도로 20\n내용 : 간판 철거\n방문 일정 : 2099년 9월 25일 오전 11시'),
        }
        for field, (name, text) in examples.items():
            with self.subTest(field=field):
                if field == 'kakao_file':
                    text = ('이전 대화\n' * 35000) + text
                status, body = asyncio.run(asgi_multipart_request(self.app, '/schedule-import',
                    fields={'csrf_token': self.csrf()}, files={field: (name, text.encode('utf-8'))}))
                self.assertEqual(status, 200)
                self.assertIn('일정 1건'.encode('utf-8'), body)
        self.assertEqual(len(self.app.state.repo.list_sources(self.app.state.workspace_id)), 2)
        for files in ({}, {'kakao_file': ('empty.txt', b'  ')}, {'markdown_file': ('bad.md', b'\xff')},
                      {'kakao_file': ('large.txt', b'a' * 2_000_001)}):
            status, _ = asyncio.run(asgi_multipart_request(self.app, '/schedule-import',
                fields={'csrf_token': self.csrf()}, files=files))
            self.assertEqual(status, 422)

    def test_dashboard_is_local_secure_and_fictional(self) -> None:
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertEqual(headers["x-frame-options"], "DENY")
        decoded = body.decode("utf-8")
        self.assertIn("가상 데모 모드", decoded)
        self.assertIn("오늘 작업", decoded)
        self.assertIn("오늘 견적 방문", decoded)
        self.assertIn("업무 자료 넣기", decoded)
        self.assertIn('href="/records"', decoded)
        self.assertIn('href="/business"', decoded)
        self.assertNotIn("검토함", decoded)

        bad_status, _, _ = self.request("/", host="outside.example")
        self.assertEqual(bad_status, 400)

    def test_new_product_structure_has_records_and_business_without_review_navigation(self) -> None:
        records_status, _, records_body = self.request("/records")
        business_status, _, business_body = self.request("/business")
        self.assertEqual((records_status, business_status), (200, 200))
        records_text = records_body.decode("utf-8")
        business_text = business_body.decode("utf-8")
        self.assertIn("업무 자료 넣기", records_text)
        self.assertIn("오늘의 작업일지", records_text)
        self.assertIn("틀린 것만 수정", records_text)
        self.assertIn("견적과 비용 보기", business_text)
        self.assertIn("현장 참고 열기", business_text)
        self.assertNotIn("검토함", records_text + business_text)
        self.assertNotIn("미수금", records_text + business_text)

    def test_site_detail_starts_with_briefing_and_collapses_management_sections(self) -> None:
        site_id = self.app.state.repo.list_sites(self.app.state.workspace_id)[0]["id"]
        status, _, body = self.request(f"/sites/{site_id}")
        self.assertEqual(status, 200)
        decoded = body.decode("utf-8")
        self.assertIn("지금 이 현장", decoded)
        self.assertIn("다음 일정", decoded)
        self.assertIn("다음 할 일", decoded)
        self.assertIn("현장 상세 기록 보기", decoded)
        self.assertIn("직접 수정·추가", decoded)
        self.assertNotIn("현재 검토 완료", decoded)
        self.assertNotIn("미수금", decoded)

    def test_daily_explicit_expenses_reach_ledger_once(self):
        form = {'csrf_token': self.csrf(), 'site_name': '자동 지출 시험', 'log_date': '2026-09-10',
                'entry_text': '주유비 7만원 지불, 점심 3만원 결제. 내일 장비비 80만원 지급 예정'}
        for _ in range(2):
            status, _, _ = self.request('/daily-log', method='POST', form=form)
            self.assertEqual(status, 303)
        site = next(s for s in self.app.state.repo.list_sites(self.app.state.workspace_id) if s['name'] == '자동 지출 시험')
        rows = self.app.state.repo.list_for_site(site['id'], 'money_item')
        posted = [r for r in rows if r['review_status'] == 'approved']
        self.assertEqual(len(posted), 2)
        self.assertEqual(sum(r['amount_krw'] for r in posted), 100000)
        self.assertTrue(all(r['actualness'] == 'actual' and r['evidence_ref'] for r in posted))
        status, _, body = self.request('/ledger?filter=cost')
        self.assertEqual(status, 200)
        self.assertIn('주유비 7만원 지불', body.decode('utf-8'))

    def test_worker_rate_save_and_historical_display(self):
        status, _, _ = self.request('/worker-rates', method='POST', form={
            'csrf_token': self.csrf(), 'name': '아마라', 'amount': '150000', 'effective_on': '2026-09-01'})
        self.assertEqual(status, 303)
        self.assertEqual(self.app.state.repo.list_worker_rates(self.app.state.workspace_id)[0]['amount'], 150000)
        site = self.app.state.repo.create_site(self.app.state.workspace_id, '일당 연결 시험')
        self.app.state.repo.create_event(site['id'], 'daily_work_log', '작업 일지',
                                        description='아마라 반나절 일했음', occurred_at='2026-09-10T18:00+09:00')
        status, _, body = self.request('/sites/' + site['id'])
        self.assertEqual(status, 200)
        self.assertIn('75,000', body.decode('utf-8'))
        self.assertEqual(self.app.state.repo.list_for_site(site['id'], 'money_item'), [])

    def test_site_detail_builds_briefing_from_existing_work_log(self) -> None:
        site = self.app.state.repo.create_site(
            self.app.state.workspace_id, "브리핑 연결 시험 현장", business_status="in_progress"
        )
        self.app.state.repo.create_event(
            site["id"], "daily_work_log", "오늘 작업 기록",
            description="[오늘의 철거 기록]\n현장: 브리핑 연결 시험 현장\n오늘 한 일:\n가벽을 철거하고 폐기물을 밖으로 반출함.",
            occurred_at=datetime.now(timezone(timedelta(hours=9))).isoformat(),
            epistemic_type="fact", review_status="approved", actor_type="user",
        )
        status, _, body = self.request(f"/sites/{site['id']}")
        self.assertEqual(status, 200)
        decoded = body.decode("utf-8")
        self.assertIn("가벽을 철거하고 폐기물을 밖으로 반출함.", decoded)
        self.assertIn("최근 작업일지에서 자동 요약", decoded)
        self.assertIn("기록 기반 요약", decoded)
        self.assertIn("아직 확인되지 않은 내용", decoded)

    def test_schedule_pages_and_overlapping_posts(self) -> None:
        site_id = self.app.state.repo.list_sites(self.app.state.workspace_id)[0]["id"]
        token = self.csrf()
        target_day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        work_status, _, work_body = self.request("/work-schedules")
        visit_status, _, visit_body = self.request("/estimate-visits")
        self.assertEqual((work_status, visit_status), (200, 200))
        self.assertIn("작업 일정".encode(), work_body)
        self.assertIn("견적 방문".encode(), visit_body)
        status, _, _ = self.request(
            "/work-schedules", method="POST",
            form={"csrf_token": token, "site_id": site_id, "start_at": f"{target_day}T08:00",
                  "end_at": f"{target_day}T17:00", "summary": "철거 작업", "participants": "", "notes": ""},
        )
        self.assertEqual(status, 303)

        schedule = next(
            row for row in self.app.state.repo.list_schedule_items(self.app.state.workspace_id)
            if row["summary"] == "철거 작업"
        )
        detail_status, _, detail_body = self.request(f"/schedules/{schedule['id']}")
        self.assertEqual(detail_status, 200)
        self.assertIn(schedule["summary"].encode("utf-8"), detail_body)
        dashboard_status, _, dashboard_body = self.request("/")
        self.assertEqual(dashboard_status, 200)
        self.assertIn(f'/schedules/{schedule["id"]}'.encode("utf-8"), dashboard_body)
        status, _, _ = self.request(
            "/estimate-visits", method="POST",
            form={"csrf_token": token, "start_at": f"{target_day}T10:00", "customer_name": "고객",
                  "address": "시흥시", "summary": "범위 확인", "contact": "", "notes": ""},
        )
        self.assertEqual(status, 303)

    def test_estimate_visit_calendar_selects_one_day_and_hides_manual_form(self) -> None:
        self.app.state.repo.create_schedule_item(
            self.app.state.workspace_id, "estimate_visit", "가상 월간 방문",
            "2026-09-24T14:00+09:00", "전체 철거 범위 확인", address_text="가상 주소",
        )
        status, _, body = self.request("/estimate-visits?month=2026-09&day=2026-09-24")
        self.assertEqual(status, 200)
        decoded = body.decode("utf-8")
        self.assertIn("2026년 9월", decoded)
        self.assertIn('aria-label="2026-09-24, 방문 1건"', decoded)
        self.assertIn("9월 24일", decoded)
        self.assertIn("가상 월간 방문", decoded)
        self.assertIn("직접 방문 일정 추가", decoded)
        self.assertNotIn('<details class="form-disclosure visit-manual" open', decoded)

    def test_ledger_collects_site_money_without_mixing_estimates_into_actual_totals(self) -> None:
        site_id = self.app.state.repo.list_sites(self.app.state.workspace_id)[0]["id"]
        self.app.state.repo.create_money_item(
            site_id, "ledger-sky-test", "스카이차 기사 비용", 300_000,
            "other", "confirmed", "actual", "outflow",
        )
        status, _, body = self.request("/ledger")
        self.assertEqual(status, 200)
        decoded = body.decode("utf-8")
        self.assertIn("사업 장부", decoded)
        self.assertIn("견적금액", decoded)
        self.assertIn("확정 지출", decoded)
        self.assertIn("현재까지 남은 금액", decoded)
        self.assertIn("인건비", decoded)
        self.assertIn("폐기물 처리 비용", decoded)
        self.assertIn("장부는 자동으로 정리됩니다", decoded)
        self.assertNotIn("오늘의 철거 기록에서 정리한 후보", decoded)
        cost_status, _, cost_body = self.request("/ledger?filter=cost")
        self.assertEqual(cost_status, 200)
        cost_decoded = cost_body.decode("utf-8")
        self.assertIn("장비·기사 포함", cost_decoded)
        self.assertIn("왈가닥", cost_decoded)
        self.assertIn("종합폐기물", cost_decoded)
        self.assertIn("고물 처리", cost_decoded)
        self.assertIn("자재·소모품비", cost_decoded)
        self.assertIn("식비", cost_decoded)
        self.assertIn("음료비", cost_decoded)
        self.assertIn("차량·이동비", cost_decoded)
        equipment_start = cost_decoded.index("장비·기사 포함")
        waste_start = cost_decoded.index("폐기물 처리 비용", equipment_start)
        self.assertIn("스카이차 기사 비용", cost_decoded[equipment_start:waste_start])
        self.assertNotIn("계약금 입금", decoded)
        planned_status, _, planned_body = self.request("/ledger?filter=quote")
        self.assertEqual(planned_status, 200)
        self.assertIn("기본 철거·복원 합의금", planned_body.decode("utf-8"))

    def test_unknown_money_post_stays_null(self) -> None:
        site_id = self.app.state.repo.list_sites(self.app.state.workspace_id)[0]["id"]
        status, headers, _ = self.request(
            f"/sites/{site_id}/money",
            method="POST",
            form={
                "csrf_token": self.csrf(),
                "purpose": "waste_cost",
                "direction": "out",
                "actualness": "estimated",
                "progression": "candidate",
                "amount": "",
                "occurred_on": "",
                "note": "금액을 아직 모르는 가상 폐기물 비용",
                "epistemic_status": "claim",
                "review_status": "pending",
                "source_note": "",
            },
        )
        self.assertEqual(status, 303)
        self.assertIn("notice=money_created", headers["location"])
        rows = self.app.state.repo.list_for_site(site_id, "money_item")
        created = next(row for row in rows if row["title"].startswith("금액을 아직 모르는"))
        self.assertIsNone(created["amount_krw"])

    def test_ledger_direct_expense_and_money_candidate_correction(self) -> None:
        site_id = self.app.state.repo.list_sites(self.app.state.workspace_id)[0]["id"]
        page_status, _, page_body = self.request("/ledger/expense/new")
        self.assertEqual(page_status, 200)
        self.assertIn("어디에 쓴 돈인가요?", page_body.decode("utf-8"))
        status, headers, _ = self.request(
            "/ledger/expense/new", method="POST",
            form={"csrf_token": self.csrf(), "site_id": site_id, "category": "food",
                  "amount": "30000", "occurred_on": "2026-09-09", "note": "현장 점심"},
        )
        self.assertEqual(status, 303)
        self.assertIn("notice=expense_created", headers["location"])
        created = next(
            row for row in self.app.state.repo.list_for_site(site_id, "money_item")
            if row["title"].startswith("식비 · 현장 점심")
        )
        self.assertEqual(created["amount_krw"], 30_000)
        self.assertEqual(created["review_status"], "approved")

        candidate = self.app.state.repo.create_money_item(
            site_id, "wrong-unit-price", "고물 단가를 총액으로 잘못 읽음", 200,
            "waste_cost", "candidate", "actual", "outflow",
            notes="고물 30만원, 다른 고물상은 KG당 200원", epistemic_type="claim",
            review_status="pending", actor_type="ai", evidence_ref="evidence:test",
        )
        status, headers, _ = self.request(
            f"/review/{candidate['id']}/money-correction", method="POST",
            form={"csrf_token": self.csrf(), "amount": "300000", "purpose": "scrap_income",
                  "note": "고물 판매수익", "reason": "200원은 단가이고 총액은 30만원"},
        )
        self.assertEqual(status, 303)
        self.assertIn("notice=money_candidate_updated", headers["location"])
        current = self.app.state.repo.list_for_site(site_id, "money_item")
        replacement = next(row for row in current if row.get("supersedes_money_item_id") == candidate["id"])
        self.assertEqual(replacement["amount_krw"], 300_000)
        self.assertEqual(replacement["purpose"], "scrap_income")
        self.assertEqual(replacement["review_status"], "approved")

    def test_review_and_backup_posts_use_csrf(self) -> None:
        pending = self.app.state.repo.pending_reviews(self.app.state.workspace_id)
        candidate = pending[0]
        status, _, _ = self.request(
            f"/review/{candidate['id']}",
            method="POST",
            form={
                "csrf_token": self.csrf(),
                "target_type": candidate["target_type"],
                "decision": "approved",
                "reason": "가상 원문과 대조해 확인함",
            },
        )
        self.assertEqual(status, 303)

        backup_status, _, _ = self.request(
            "/backups", method="POST", form={"csrf_token": self.csrf()}
        )
        self.assertEqual(backup_status, 303)
        self.assertEqual(len(list(self.app.state.settings.backups_dir.glob("field-brain-*.db"))), 1)

    def test_integrated_capture_preserves_source_and_creates_pending_candidates(self) -> None:
        site_id = self.app.state.repo.list_sites(self.app.state.workspace_id)[0]["id"]
        before = len(self.app.state.repo.pending_reviews(self.app.state.workspace_id))
        status, headers, _ = self.request(
            f"/sites/{site_id}/capture",
            method="POST",
            form={
                "csrf_token": self.csrf(),
                "capture_kind": "work_note",
                "raw_text": (
                    "가상 화장실 철거 작업을 완료함.\n"
                    "폐기물 처리비 실제 40만원을 지불함.\n"
                    "토요일에 고객에게 잔금 일정을 확인해야 함.\n"
                    "작업 범위 번복 위험이 있어 문자 확정 필요."
                ),
            },
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], f"/sites/{site_id}?notice=capture_created")
        sources = self.app.state.repo.list_sources(self.app.state.workspace_id, site_id=site_id)
        self.assertTrue(sources)
        self.assertTrue(list(Path(self.app.state.settings.originals_dir).glob("text/*/*/*.txt")))
        after_rows = self.app.state.repo.pending_reviews(self.app.state.workspace_id)
        self.assertGreater(len(after_rows), before)
        captured = [row for row in after_rows if str(row.get("evidence_ref", "")).startswith("evidence:")]
        self.assertTrue({row["target_type"] for row in captured}.issuperset({"event", "task", "money_item", "risk"}))

        review_status, _, review_body = self.request("/review")
        self.assertEqual(review_status, 200)
        self.assertIn("이 값을 찾은 문장", review_body.decode("utf-8"))

    def test_imported_work_leads_to_same_site_record_and_actual_expense(self) -> None:
        chat = ('2099년 9월 2일 오전 9:00, 이준희 과장 청년철거 : <공사 일정 픽스입니다>\n'
                '주소 : 가상시 연결로 7\n내용 : 가벽 철거\n공사 일정 : 9월 3일 오전 9시\n업체 실행비 : 100만원')
        status, _ = asyncio.run(asgi_multipart_request(self.app, '/schedule-import',
            fields={'csrf_token': self.csrf()}, files={'kakao_file': ('flow.txt', chat.encode('utf-8'))}))
        self.assertEqual(status, 200)
        repo, workspace = self.app.state.repo, self.app.state.workspace_id
        item = next(r for r in repo.list_schedule_items(workspace) if r['address_text'] == '가상시 연결로 7')
        site_id = item['site_id']
        self.assertEqual(repo.list_for_site(site_id, 'money_item'), [])
        status, _, body = self.request('/schedules/' + item['id'])
        self.assertEqual(status, 200)
        self.assertIn(('/daily-log?site_id=' + site_id).encode(), body)
        self.assertIn(('/sites/' + site_id).encode(), body)
        form = {'csrf_token': self.csrf(), 'site_id': site_id, 'log_date': '2099-09-03',
                'entry_text': '가벽을 철거했다. 주유비 7만원 지불.'}
        status, headers, _ = self.request('/daily-log', method='POST', form=form)
        self.assertEqual(status, 303)
        self.assertIn(site_id, headers['location'])
        amounts = [r['amount_krw'] for r in repo.list_for_site(site_id, 'money_item')]
        self.assertIn(70000, amounts)
        self.assertNotIn(1000000, amounts)
        self.assertEqual(len(repo.list_sources(workspace, site_id=site_id)), 1)
        repo.set_schedule_cancelled(item['id'], cancelled=True, reason='시험 취소')
        self.assertNotIn(('/daily-log?site_id=' + site_id).encode(), self.request('/schedules/' + item['id'])[2])

    def test_daily_log_keeps_selected_site_even_with_duplicate_names(self) -> None:
        repo, workspace = self.app.state.repo, self.app.state.workspace_id
        first = repo.create_site(workspace, '동명 시험 현장', address_text='가상로 1')
        second = repo.create_site(workspace, '동명 시험 현장', address_text='가상로 2')
        status, _, body = self.request('/daily-log?site_id=' + second['id'])
        self.assertEqual(status, 200)
        self.assertIn(('value="' + second['id'] + '"').encode(), body)
        form = {'csrf_token': self.csrf(), 'site_name': first['name'],
                'log_date': '2026-09-21', 'entry_text': '가벽을 철거했다.'}
        status, _, _ = self.request('/daily-log', method='POST', form=form)
        self.assertEqual(status, 422)
        self.assertEqual(repo.list_sources(workspace, site_id=first['id']), [])
        form.update(site_id=second['id'], site_name='변조된 이름')
        status, headers, _ = self.request('/daily-log', method='POST', form=form)
        self.assertEqual(status, 303)
        self.assertIn(second['id'], headers['location'])
        self.assertEqual(len(repo.list_sources(workspace, site_id=second['id'])), 1)
        self.assertEqual(repo.list_sources(workspace, site_id=first['id']), [])
        other = repo.create_workspace('별도 작업공간')
        foreign = repo.create_site(other['id'], '외부 현장')
        self.assertEqual(self.request('/daily-log?site_id=' + foreign['id'])[0], 404)
        form['site_id'] = foreign['id']
        self.assertEqual(self.request('/daily-log', method='POST', form=form)[0], 404)

    def test_daily_log_tab_saves_structured_original_to_site_timeline(self) -> None:
        page_status, _, page_body = self.request("/daily-log")
        self.assertEqual(page_status, 200)
        decoded = page_body.decode("utf-8")
        self.assertIn("오늘의 철거 기록", decoded)
        self.assertIn("오늘 이야기를 그대로 적어 주세요", decoded)
        self.assertIn("생각이 안 날 때만 참고하세요", decoded)
        self.assertIn("아래 내용을 구분해서 쓰거나 모두 적을 필요는 없습니다", decoded)
        self.assertNotIn('name="work_done"', decoded)

        status, headers, _ = self.request(
            "/daily-log", method="POST",
            form={
                "csrf_token": self.csrf(),
                "site_name": "가상 청주 모텔 철거",
                "log_date": "2026-09-02",
                "entry_text": (
                    "아마라와 객실 집기와 목재를 반출했고 2.5톤 화물차를 한 번 썼다. "
                    "목재는 현장에 분리 적재했다. 사장님은 내일 물량을 미리 알려 달라고 했다. "
                    "목재 물량이 예상보다 많았고 내일 잔여 목재를 반출해야 한다. "
                    "목재 처리 단가는 확인이 필요하다."
                ),
            },
        )
        self.assertEqual(status, 303)
        self.assertIn("notice=daily_log_created", headers["location"])
        site = next(
            row for row in self.app.state.repo.list_sites(self.app.state.workspace_id)
            if row["name"] == "가상 청주 모텔 철거"
        )
        sources = self.app.state.repo.list_sources(self.app.state.workspace_id, site_id=site["id"])
        self.assertEqual(sources[-1]["source_type"], "manual_input")
        events = self.app.state.repo.list_for_site(site["id"], "event")
        log = next(row for row in events if row["event_type"] == "daily_work_log")
        self.assertIn("목재 처리 단가는 확인이 필요하다", log["description"])
        self.assertEqual(log["review_status"], "approved")

    def test_estimate_result_form_saves_visit_outcome_without_assignment_or_status(self) -> None:
        visit = self.app.state.repo.create_schedule_item(
            self.app.state.workspace_id, "estimate_visit", "가상 화성 식당",
            "2026-09-18T15:00+09:00", "42평 전체 철거", address_text="화성시",
        )
        page_status, _, page_body = self.request(f"/schedules/{visit['id']}")
        self.assertEqual(page_status, 200)
        decoded = page_body.decode("utf-8")
        self.assertIn("견적 방문 결과", decoded)
        self.assertNotIn("방문 담당자", decoded)
        self.assertNotIn("방문 상태", decoded)
        status, headers, _ = self.request(
            f"/schedules/{visit['id']}/estimate-result", method="POST",
            form={
                "csrf_token": self.csrf(), "customer_requests": "가벽과 바닥 철거",
                "work_plan": "1일차 기공2 조공2", "labor_cost_krw": "3650000",
                "equipment_plan": "사다리차", "equipment_cost_krw": "750000",
                "waste_plan": "통합폐기물", "waste_cost_krw": "2100000",
                "restoration_plan": "샷시 복원", "restoration_cost_krw": "3100000",
                "total_quote_krw": "13300000", "conditions_text": "필름 미제거 시 감액",
            },
        )
        self.assertEqual(status, 303)
        self.assertIn("notice=estimate_result_saved", headers["location"])
        saved = self.app.state.repo.get_estimate_visit_result(visit["id"])
        self.assertEqual(saved["total_quote_krw"], 13_300_000)

    def test_visit_agreement_posts_atomically_and_preserves_invalid_text(self) -> None:
        repo = self.app.state.repo
        site = repo.create_site(self.app.state.workspace_id, '가상 수주 현장')
        visit = repo.create_schedule_item(self.app.state.workspace_id, 'estimate_visit', '가상 수주 현장',
            '2099-09-12T11:00+09:00', '가벽 철거', site_id=site['id'])
        path = f"/schedules/{visit['id']}/agreement"
        status, _, body = self.request(path, method='POST',
            form={'csrf_token': self.csrf(), 'agreement_note': '480만원 메이드 예정'})
        self.assertEqual(status, 422)
        self.assertIn('480만원 메이드 예정', body.decode('utf-8'))
        self.assertEqual(repo.list_for_site(site['id'], 'money_item'), [])
        status, headers, _ = self.request(path, method='POST',
            form={'csrf_token': self.csrf(), 'agreement_note': '480만원 메이드. 2099-09-20 08:00 작업 시작'})
        self.assertEqual(status, 303)
        self.assertIn(f"/sites/{site['id']}", headers['location'])
        status, _, body = self.request(headers['location'])
        self.assertEqual(status, 200)
        self.assertIn('4,800,000원', body.decode('utf-8'))
        self.assertEqual(len([row for row in repo.list_schedule_items(self.app.state.workspace_id, schedule_type='work') if row['site_id'] == site['id']]), 1)
        status, _, _ = self.request(path, method='POST', form={'agreement_note': '480만원 메이드'})
        self.assertEqual(status, 403)

    def test_free_visit_note_is_visible_on_linked_site(self) -> None:
        repo = self.app.state.repo
        workspace = self.app.state.workspace_id
        site = repo.create_site(workspace, '가상 연결 상가')
        visit = repo.create_schedule_item(
            workspace, 'estimate_visit', '가상 연결 상가', '2099-09-18T11:00+09:00',
            '샷시 보존 방문', site_id=site['id'],
        )
        status, _, _ = self.request(
            f"/schedules/{visit['id']}/estimate-result", method='POST',
            form={'csrf_token': self.csrf(), 'customer_requests': '샷시는 남기고 가벽만 철거.\n고객과 협의 중.'},
        )
        self.assertEqual(status, 303)
        status, _, body = self.request(f"/sites/{site['id']}")
        self.assertEqual(status, 200)
        html = body.decode('utf-8')
        self.assertIn('샷시는 남기고 가벽만 철거.', html)
        self.assertIn('샷시 보존 방문', html)
        self.assertIn('제안 견적은 수주금액에 합산하지 않습니다.', html)
        self.assertIn(f"/schedules/{visit['id']}#estimate-result", html)

    def test_schedule_detail_can_reschedule_cancel_and_restore(self) -> None:
        visit = self.app.state.repo.create_schedule_item(
            self.app.state.workspace_id, "estimate_visit", "가상 약국",
            "2026-09-18T12:00+09:00", "바닥과 간판 철거", address_text="가상 주소",
        )
        page_status, _, page_body = self.request(f"/schedules/{visit['id']}")
        self.assertEqual(page_status, 200)
        decoded = page_body.decode("utf-8")
        self.assertIn("날짜·시간 바꾸기", decoded)
        self.assertIn("일정 취소하기", decoded)

        changed_status, changed_headers, _ = self.request(
            f"/schedules/{visit['id']}/reschedule", method="POST",
            form={"csrf_token": self.csrf(), "start_at": "2026-09-19T13:30", "end_at": "",
                  "reason": "고객 요청으로 변경"},
        )
        self.assertEqual(changed_status, 303)
        self.assertIn("schedule_rescheduled", changed_headers["location"])
        self.assertEqual(self.app.state.repo.get_schedule_item(visit["id"])["start_at"], "2026-09-19T13:30+09:00")

        cancelled_status, _, _ = self.request(
            f"/schedules/{visit['id']}/cancel", method="POST",
            form={"csrf_token": self.csrf(), "cancelled": "1", "reason": "고객 취소"},
        )
        self.assertEqual(cancelled_status, 303)
        self.assertEqual(self.app.state.repo.get_schedule_item(visit["id"])["business_status"], "cancelled")
        cancelled_page, _, cancelled_body = self.request(f"/schedules/{visit['id']}")
        self.assertEqual(cancelled_page, 200)
        self.assertIn("일정 다시 살리기".encode("utf-8"), cancelled_body)

        restored_status, _, _ = self.request(
            f"/schedules/{visit['id']}/cancel", method="POST",
            form={"csrf_token": self.csrf(), "cancelled": "0", "reason": "다시 방문 확정"},
        )
        self.assertEqual(restored_status, 303)
        self.assertEqual(self.app.state.repo.get_schedule_item(visit["id"])["business_status"], "scheduled")

    def test_estimate_standard_can_be_saved_and_archived(self) -> None:
        page_status, _, page_body = self.request("/field-reference")
        self.assertEqual(page_status, 200)
        self.assertIn("현장 참고".encode(), page_body)
        self.assertIn("마루 철거 단가".encode(), page_body)
        status, headers, _ = self.request(
            "/estimate-standards", method="POST",
            form={"csrf_token": self.csrf(), "category": "margin", "title": "하루 마진",
                  "rule_text": "소규모 현장은 하루 50~100만원", "rationale": "운영 기준",
                  "source_note": "현장 대화"},
        )
        self.assertEqual(status, 303)
        self.assertIn("notice=estimate_standard_created", headers["location"])
        standard = next(row for row in self.app.state.repo.list_estimate_standards(self.app.state.workspace_id) if row["title"] == "하루 마진")
        status, _, _ = self.request(
            f"/estimate-standards/{standard['id']}/active", method="POST",
            form={"csrf_token": self.csrf(), "is_active": "0"},
        )
        self.assertEqual(status, 303)
        self.assertNotIn(standard["id"], {row["id"] for row in self.app.state.repo.list_estimate_standards(self.app.state.workspace_id)})


if __name__ == "__main__":
    unittest.main()
