import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def users(db):
    tutor = User(email="p2-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="p2-school@example.com", role="school", roles=["school"], display_name="学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    sales = User(email="p2-sales@example.com", role="sales", roles=["sales"], display_name="営業", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    office = User(email="p2-office@example.com", role="office", roles=["office"], display_name="事務", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    master = User(email="p2-master@example.com", role="admin_master", roles=["admin_master"], display_name="管理者", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school, sales, office, master])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, student_name="第2弾生徒", system_type="new")
    db.add(assignment)
    db.flush()
    db.add(WorkAssignmentProfile(assignment_id=assignment.id, tutor_id=tutor.id, school_id=school.id, form_type="monthly_dispatch"))
    db.commit()
    return {"tutor": tutor, "school": school, "sales": sales, "office": office, "master": master, "assignment": assignment}


@pytest.fixture()
def client():
    return TestClient(app)


def _login(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_report(client, users, month="2026-06"):
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": str(users["assignment"].id),
            "target_month": month,
            "form_type": "monthly_dispatch",
            "form_data": {"lines": [{"date": f"{month}-01", "teach_minutes": 90, "break_minutes": 10, "commute_fee": 700}]},
        },
        headers=_login(client, "p2-tutor@example.com"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


class TestPhase2Pages:
    @pytest.mark.parametrize(
        ("path", "email"),
        [
            ("/tutor/approval", "p2-tutor@example.com"),
            ("/sales/queue", "p2-sales@example.com"),
            ("/office/queue", "p2-office@example.com"),
            ("/finance/queue", "p2-master@example.com"),
        ],
    )
    def test_pages_redirect_when_anonymous_and_200_when_authenticated(self, client, users, path, email):
        anon = TestClient(app)
        res = anon.get(path, follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"

        _login(client, email)
        res = client.get(path)
        assert res.status_code == 200

    def test_admin_report_detail_anonymous_redirects(self, client, users):
        report_id = _create_report(client, users)
        anon = TestClient(app)
        res = anon.get(f"/admin/reports/{report_id}", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"

    def test_tutor_reports_header_layout(self, client, users):
        """業務連絡表ヘッダーのレイアウト再設計（202607201442）: ラベル上置き＋6カラムグリッド＋対象月パネル。
        入力欄の id・data-meta・data-fieldgroup は従来どおり（保存・契約ロック・表示フラグ・e2e互換）。"""
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/reports")
        assert res.status_code == 200
        # 2ゾーン構成: フォーム群（左）＋対象月パネル（xl以上=右カラム・モバイル=先頭）
        assert "xl:grid-cols-[minmax(0,1fr)_17rem]" in res.text
        # 追加修正: ヘッダーは親コンテナの右端まで100%広がる（固定max-widthなし）
        assert "max-w-6xl" not in res.text
        # 追加修正2: 対象月はカードにせず他の項目とラベル・入力欄の高さを揃える（プレーンな右カラム）
        assert '<aside class="xl:order-2">' in res.text
        assert "justify-center rounded-lg border border-slate-200 bg-slate-50" not in res.text
        # 追加修正2: 「月分業務連絡表」→「業務連絡表」（セレクトの「〇〇月分」と月分が重複するため）
        assert ">業務連絡表</span>" in res.text
        assert "月分業務連絡表" not in res.text
        # 追加修正2: トグルは「この月は授業なし」（（長期休業等）は表示しない。HTML初期値とJS再描画の両方）
        assert ">この月は授業なし</span>" in res.text
        assert "この月は授業なし（長期休業等）" not in res.text
        # 追加修正2: コピーボタンは「前回コピー」「先月コピー」の短縮表記
        assert "前回コピー" in res.text
        assert "先月コピー" in res.text
        # ラベルは入力欄の上（label for=... 形式）＝セル内側ラベルの強制改行を廃止
        assert '<label for="dispatchPlaceSchool"' in res.text
        assert '<label for="workContent"' in res.text
        assert "事業所の<br>名称・組織単位" not in res.text
        # 入力欄の id・data-meta・data-fieldgroup は不変（JS・e2e が参照）
        for marker in [
            'id="dispatchPlaceSchool"', 'id="classroomName"', 'id="dispatchPlaceAddress"',
            'id="workLocation"', 'id="tutorNameDisplay"', 'id="tutorNo"', 'id="customerId"',
            'id="workContent"', 'id="monthFilter"', 'id="noLessonToggleWrap"', 'id="noLessonToggle"',
            'data-fieldgroup="dispatch_address"', 'data-fieldgroup="work_content"',
        ]:
            assert marker in res.text, marker

    def test_tutor_reports_page_has_mobile_line_ui(self, client, users):
        """講師の報告書一覧にスマホ入力UI（明細リスト＋詳細シート）が含まれる。
        md未満では明細テーブルを隠してリスト表示し、行タップでシートを開いて入力する。"""
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/reports")
        assert res.status_code == 200
        assert 'id="mobileLineList"' in res.text
        assert 'id="lineSheetOverlay"' in res.text
        # 明細テーブルはPC（md以上）のみ表示
        assert 'class="hidden overflow-x-auto rounded-lg border border-slate-200 md:block"' in res.text

    def test_shared_work_report_calc_core_included(self, client, users):
        """明細の入力・自動計算の共通コア(work_report_calc.js)が講師の報告書一覧と
        事務ダッシュボード（報告書修正モーダル）の両方で読み込まれ、静的配信される。
        事務修正は講師フォームと同一の入力仕様（種別・担当時限・自動計算・集計）を共有する。"""
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/reports")
        assert res.status_code == 200
        assert '/static/js/work_report_calc.js' in res.text
        # コマ設定契約の「副担当の位置」（コマ後/コマ間）列（202607161853）: 講師フォーム（行＋シート）に組み込み済み
        assert 'data-field="secondary_placement"' in res.text
        assert 'data-sheet-field="secondary_placement"' in res.text
        _login(client, "p2-office@example.com")
        res = client.get("/office/queue")
        assert res.status_code == 200
        assert '/static/js/work_report_calc.js' in res.text
        assert 'id="officeEditSummary"' in res.text  # 修正モーダルの集計欄
        # 事務修正モーダルも「副担当の位置」を講師フォームと同一仕様で持つ
        assert 'data-field="secondary_placement"' in res.text
        static = client.get("/static/js/work_report_calc.js")
        assert static.status_code == 200
        assert "WorkReportCalc" in static.text
        # 副担当の位置の計算ルール（コマ後＝隙間のまま/コマ間＝隙間−副担当）は共通コアに集約されている
        assert "secondaryPlacementIsGap" in static.text
        assert "normalizedSecondaryPlacement" in static.text

    def test_invite_form_inline_single_row(self, client, users):
        """新規ユーザー登録フォーム（202607201825）: ロール・氏名・メール・送信ボタンを1行に横並び。
        氏名/メールは等幅・ボタンは入力欄と同じ高さで下端揃え。注記はフォーム左下に維持。"""
        _login(client, "p2-office@example.com")
        res = client.get("/admin/users")
        assert res.status_code == 200
        # 4カラム1行グリッド（ロール150px・氏名/メール等幅minmax・ボタンauto）＋下端揃え
        assert "md:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)_auto]" in res.text
        assert "grid items-end gap-3" in res.text
        # 送信ボタンは入力欄と同じ高さ(h-[42px])で同一グリッド行内に配置
        assert "h-[42px]" in res.text
        assert "招待メールを送る" in res.text
        # 注記は従来どおりフォーム左下（idは不変＝ロール別文言のJSに影響なし）
        assert 'id="roleHint"' in res.text
        assert "Noは自動で割り当てられます。" in res.text

    def test_contracts_action_column_right_aligned(self, client, users):
        """契約管理（202607201825）: ＋新規登録の右端ラインと操作列（削除ボタン）の右端ラインを揃える。"""
        _login(client, "p2-office@example.com")
        res = client.get("/admin/contracts")
        assert res.status_code == 200
        # 操作列はテーブル表示時(md以上)のみ右寄せ（モバイルのカード表示は従来どおり左）
        assert "flex flex-wrap gap-2 md:justify-end" in res.text
        assert '<th class="px-4 py-3 text-left md:text-right">操作</th>' in res.text
        # ツールバーの左右パディングをテーブルのセル(px-4)と揃え、右端ラインを一直線にする
        assert "flex flex-wrap items-center justify-between gap-3 px-4" in res.text

    def test_contracts_activate_toggle_and_slot_delete(self, client, users):
        """契約管理（202607201957）: ②無効化↔有効化トグル・③コマ削除ボタン。"""
        _login(client, "p2-office@example.com")
        res = client.get("/admin/contracts")
        assert res.status_code == 200
        # ② 状態に応じて無効化/有効化を出し分け＋有効化ハンドラは /activate を呼ぶ
        assert "enableContract('${contract.id}')" in res.text
        assert "/api/w/contracts/${id}/activate" in res.text
        assert "有効化" in res.text and "無効化" in res.text
        # ③ コマ設定の各行に削除ボタン（詰め直し）
        assert 'data-period-remove="${term}"' in res.text
        assert "function removePeriodSlot(term, index)" in res.text

    def test_tutor_reports_target_month_stretch(self, client, users):
        """講師の対象月（202607201957）: 「業務連絡表」テキスト削除＋セレクトをw-fullで引き伸ばす。"""
        import re
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/reports")
        assert res.status_code == 200
        # 「業務連絡表」の固定テキスト（月分と重複していた表記）を対象月エリアから削除
        assert '<span class="text-sm font-bold text-slate-900">業務連絡表</span>' not in res.text
        # 対象月セレクトは w-full で右へ引き伸ばす（旧 w-36 固定は撤去）
        m = re.search(r'<select id="monthFilter"\s+class="([^"]*)"', res.text)
        assert m and "w-full" in m.group(1) and "w-36" not in m.group(1)

    def test_tutor_approval_accordion_affordance(self, client, users):
        """承認管理カード（202607201858）: 開閉可能をシェブロン＋ホバーで示す（文字に頼らない）。"""
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/approval")
        assert res.status_code == 200
        # ネイティブ<details>カードにホバー（シャドウ持ち上げ）と開時のシェブロン反転CSS
        assert ".accordion-card:hover" in res.text
        assert "accordion-card[open] .accordion-chevron" in res.text
        # summary に下向きシェブロンSVGを配置（開時はCSSで180°反転して上向き）
        assert "accordion-summary flex cursor-pointer list-none" in res.text
        assert 'class="accordion-chevron' in res.text
        assert "${ACCORDION_CHEVRON}" in res.text


class TestPhase2Api:
    def test_office_bulk_approve_moves_to_awaiting_sales(self, client, users):
        report_id = _create_report(client, users)
        for email, action in [
            ("p2-tutor@example.com", "submit"),
            ("p2-school@example.com", "approve"),
        ]:
            res = client.post(f"/api/w/reports/{report_id}/action", json={"action": action}, headers=_login(client, email))
            assert res.status_code == 200, res.text

        res = client.post(
            "/api/w/reports/bulk-action",
            json={"report_ids": [report_id], "action": "approve"},
            headers=_login(client, "p2-office@example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["updated"] == 1

        res = client.get(f"/api/w/reports/{report_id}", headers=_login(client, "p2-sales@example.com"))
        assert res.json()["status"] == WorkStatus.AWAITING_SALES

    def test_tutor_monthly_summary(self, client, users):
        _create_report(client, users)
        res = client.get(
            "/api/w/reports/monthly-summary?target_month=2026-06",
            headers=_login(client, "p2-tutor@example.com"),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_reports"] == 1
        assert data["total_teach_minutes"] == 90
        assert data["total_break_minutes"] == 10
        assert data["total_commute_fee"] == 700
