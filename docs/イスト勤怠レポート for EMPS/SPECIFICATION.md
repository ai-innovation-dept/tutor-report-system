# イスト勤怠レポート for EMPS — 仕様書（SPECIFICATION）

> 旧称: 業務連絡表システム（新システム）。`new_backend/`・ポート8001。
> 本書は2システム構成のうち「イスト勤怠レポート for EMPS」の開発者向け仕様書です。
> 共通情報: データモデル `../DATA_MODEL.md` / インフラ `../INFRASTRUCTURE.md` / 引継ぎ `../HANDOFF.md` / 索引 `../README.md`
> 最終更新: 2026-06-22

イスト勤怠レポート for EMPS（旧称: 業務連絡表システム）（`new_backend/`）は、学校へ派遣された講師が月次の「業務連絡表」を作成し、**学校 → 事務 → 営業** の順で承認を得る業務システムである。指導実績報告システム（同一リポジトリのもう一方のシステム）とは別ワークフロー・別テーブル（`work_*`）で構成され、`users` / `assignments` / `invitations` / `password_reset_tokens` テーブルのみ共有する。

> 用語: 本書では「業務連絡表」は講師が作成する報告書（成果物）そのものを指す。システム名は「イスト勤怠レポート for EMPS」に統一する。

---

## 1. システム概要

### 目的

学校に派遣された講師の月次稼働（業務連絡表）を記録し、学校確認 → 社内（事務・営業）の多段階承認を経て確定する。契約（講師×学校）ごとに報告書フォームの列構成（担当業務・副業務・採点）を動的に切り替えられる点が特徴。

### システム構成図

```
+--------------------------------------------------------------------+
|                         ブラウザ (クライアント)                      |
|  Jinja2 テンプレート + Tailwind CSS + バニラ JavaScript              |
+---------------------------+----------------------------------------+
                            | HTTP (Cookie 認証 / 共通 /api/auth)
                            v
+--------------------------------------------------------------------+
|        FastAPI アプリケーション (new_backend, port 8001)            |
|  pages router (HTML)  |  API routers /api/w/*  |  APScheduler        |
|                       |  (reports/users/admin/ |  (月末リマインダ    |
|                       |   assignments/contracts|   09:00 / stale     |
|                       |   /invitations/chat)   |   check 06:00 JST)  |
+----------------------------+---------------------------+-----------+
                             | SQLAlchemy (psycopg)      | aiosmtplib
                             v                           v
              +---------------------------+   +----------------------+
              | PostgreSQL 16 (port 5432) |   |  MailHog (port 1025) |
              | tutor DB（指導実績報告と  |   |  (開発用 SMTP)        |
              | 共有）work_* テーブル + 共有|  +----------------------+
              +---------------------------+
```

認証 Cookie は `w_access_token`（指導実績報告システムの `access_token` と分離）。使用ロールの保持は `w_selected_role`。

### 指導実績報告システムとの主な違い

| 観点 | 指導実績報告システム | イスト勤怠レポート for EMPS |
|------|-----------|-----------|
| 報告書の単位 | 指導日ごと1レコード（lesson_reports） | 紐付け×月で1レコード（work_reports、明細は form_data の JSONB） |
| 承認フロー | 講師→保護者→受付→再鑑→最終 | 講師→学校→事務→**営業承認＝完了**（経理ステップは廃止） |
| フォーム | 固定項目（日付・時刻・科目・内容） | 契約に応じた動的列定義（担当業務①〜③・副業務①〜⑤・採点） |
| ロール | tutor / parent / admin_receiver / admin_reviewer / admin_master | tutor / school / office / sales / admin_master（経理）/ admin_chief |
| API プレフィックス | `/api/...` | `/api/w/...`（認証のみ `/api/auth` を共有） |
| テーブル | 専用テーブル群 | `work_*` プレフィックス群（共有テーブルは追加カラムのみ） |

---

## 2. 登場人物とロール

| ロール | 呼称 | 主な役割 |
|--------|------|---------|
| `tutor` | 講師 | 業務連絡表の作成・編集・提出・再提出。下書き／差戻し中の削除。提出後は差戻し要求（§3.6） |
| `school` | 学校 | 講師の業務連絡表を承認／差戻し（差戻し先は講師）。学校確認待ちの差戻し要求の許可・却下 |
| `office` | 事務 | 学校承認後の確認、報告書の修正（office-edit）、超過時の事前確認。営業からの差戻し（returned_to_office）を受けて前進または講師へ差戻し。事務工程の差戻し要求の許可・却下 |
| `sales` | 営業 | 事務確認後の**最終承認（＝完了）**／差戻し（差戻し先は事務）。完了後の修正依頼（差戻し）も担当。営業工程・完了後の差戻し要求の許可・却下 |
| `admin_master` | 経理（管理者） | 承認フローの外。全報告書の参照・PDF出力、ユーザー管理（学校承認スキップ設定を含む）・契約管理・招待。月末リマインダ等の運用 |
| `admin_chief` | 管理責任者 | `admin_master` と同等＋管理責任者専用操作（学校承認スキップの手動実行 `skip_school` 等） |

> 重要: 承認フローの最終承認者は **営業（sales）** である。経理（admin_master）はフロー外の管理者であり、最終承認者ではない（旧仕様の「経理が最終承認」は廃止）。`admin_master` / `admin_chief` は参照・管理・運用を担う。

### 権限の要点

- 講師は自分の `assignment` に紐づく報告書のみ操作可能。提出先（派遣先学校）は**経理（契約管理）に登録された自分の契約校のみ**から選択する。
- 学校は自校（`assignment.parent_id` が自分）の報告書を全ステータス参照可能。
- 事務・営業・経理・管理責任者は全報告書を取得し、各画面で自ロールのキュー（`current_approver_role`）に絞り込んで表示する。
- ユーザー管理・契約管理・招待は `admin_master` が中心。承認フロー変更に伴い `/admin/users`・`/admin/contracts`・`/admin/stale-reports` の各画面は **`sales` / `office` / `admin_chief` にも開放**されている（API 側の認可は各エンドポイントの `require_role(...)` を参照）。一部 profiles の作成・更新には `office` も参加する。
- 学校承認スキップは `/admin/users` の学校詳細（`users.skip_parent_approval`）で設定する。担当紐付けは契約管理および講師の業務連絡表作成時に自動生成される（旧「システム設定」画面は廃止）。
- 兼務（事務かつ営業）スタッフには**職務分掌**が適用され、同一講師に対し事務工程と営業工程の両方の判断（承認・差戻し・差戻し要求の許可/却下）はできない（`workflow_service._assert_duty_separation`）。`admin_master` / `admin_chief` は対象外。
- 複数ロールを持つユーザーはログイン後にロール選択（`/select-role`、API `POST /api/auth/select-role`）を行う。

---

## 3. 業務フロー（ワークフロー）

ワークフローは `new_backend/app/workflow/definitions.py` の `TRANSITIONS` テーブルが唯一の定義源。アクションは `submit` / `approve` / `return` / `skip_school` / `close` に加え、講師起点の差戻し要求 `request_return` / `approve_return_request` / `decline_return_request`（§3.6）。`return`・`request_return`・`decline_return_request` はコメント必須。遷移の適用とステータス書き換えは `new_backend/app/workflow/engine.py` が行う。

### 3.1 通常フロー（営業承認で完了）

```
【講師】        【学校】       【事務】       【営業】
  draft
   | submit
   v
awaiting_school
   | approve(学校)
   v
awaiting_office
   | approve(事務)
   v
awaiting_sales
   | approve(営業)        ← 営業承認が最終承認
   v
approved（完了）
```

> 経理（admin_master）の最終承認ステップ（旧 `awaiting_finance`）は**廃止**された。営業（sales）が `awaiting_sales` を承認した時点で `approved`（完了）となる。

### 3.2 学校承認スキップ

学校承認が不要な学校（`users.skip_parent_approval = True`：学校ユーザー単位、経理のユーザー管理画面で設定）では、提出時に学校確認を飛ばして事務確認へ進む（`engine.apply_transition` が `submit` の遷移先を `awaiting_school` → `awaiting_office` に差し替える）。手動でも `skip_school`（**`admin_chief` のみ**）で `draft → awaiting_office` に進められる。

### 3.3 事前確認フロー（事務の事前確認）

次の**いずれか**に該当する報告は、学校確認の前に**事務の事前確認**を挟む（提出時に `engine.precheck_reasons` が判定し、`submit` の遷移先を `awaiting_school` → `awaiting_office_precheck` に差し替える）。

1. **月分超過**（`engine.exceeds_monthly_limit`）: 担当業務（`task_minutes_N`）の対象月の合計が、契約の月分固定（`workload_cases` の該当ケース）を超過。判定対象は担当業務のみ（週コマ・副業務・ケース未登録の業務は対象外）。
2. **1〜9分単位の手入力**（`engine.has_minute_level_input`）: 担当業務（`task_minutes_N` / デフォルト列 `teach_minutes`）・副担当業務（`sub_minutes_N`）に**10分単位でない値（1の位が1〜9）**がある。自動計算はコマ数×50分を入力するため、1分単位の手修正がある報告書を事務が事前確認する趣旨。休憩時間・採点（分）・交通費・有給/欠勤（`kind`）行は対象外。自己都合・学校行事（`kind`）の行は担当業務が0固定だが、副担当業務は手動入力できるため判定対象に含む。

```
draft --submit(講師, 事前確認判定あり)--> awaiting_office_precheck
awaiting_office_precheck --approve(事務)--> awaiting_school   （以降は通常フローへ合流）
awaiting_office_precheck --return(事務)-->  returned_to_tutor （講師へ差戻し）
```

> 学校承認スキップ校は該当時でも事前確認を挟まず通常スキップフロー（事務確認1回）になる（スキップ判定が優先）。
> 発動理由は提出イベント（`WorkReportEvent.comment`）に「【事前確認】…」として自動記録され、運営の進捗タイムラインで確認できる。
> 事務の事前確認が承認された日時は `ReportOut.precheck_approved_at`（`awaiting_office_precheck` からの approve イベント）として返り、講師の承認管理画面では「学校へ依頼日時」に表示される。

#### 講師画面の表示（事前確認フロー時）

- **承認管理**: ステップ表示が4段階「運営へ依頼 → 学校へ依頼 → 学校承認 → 運営承認」・日時4欄（運営へ依頼日時／学校へ依頼日時／学校承認日時／運営承認日時）になり、提出ボタンは「**提出**」表記（通常は「承認依頼」）。
- **提出前ポップアップ**: 1〜9分手入力がある報告書の提出ボタン押下時（承認管理・報告書一覧・差戻し後の再依頼すべて）、承認ワークフローが変わる旨の確認ポップアップ（OK／キャンセル）を表示する（`base.html` の `showConfirmModal` ／判定は `workReportHasMinuteLevelInput`＝サーバと同一ルール）。1〜9分手入力がない通常の提出でも「承認依頼の確認」ポップアップ（提出先・対象月・提出後は修正不可の旨）を表示する＝承認依頼は必ずいずれか1つのポップアップで確認してから提出される（二重には表示しない）。報告書一覧の「承認依頼」は保存成功後に確認を出すため、キャンセルしても保存（下書きの更新）は残る。
- 1〜9分手入力はクライアントでも判定できるため提出前から事前確認フロー表示になる。月分超過（契約との突合が必要）は提出後に事前確認フロー表示へ切り替わる。

### 3.4 差戻しフロー

| 差戻し元 | アクション | 遷移 | 差戻し先 |
|----------|-----------|------|---------|
| 事務（事前確認） | return(office) | awaiting_office_precheck → `returned_to_tutor` | 講師 |
| 学校 | return(school) | awaiting_school → `returned_to_tutor` | 講師 |
| 事務 | return(office) | awaiting_office → `returned_to_tutor` | 講師 |
| 営業 | return(sales) | awaiting_sales → `returned_to_office` | 事務 |
| 営業（完了後） | return(sales) | approved → `returned_to_office` | 事務 |

> 完了（approved）後の差戻しは**営業（sales）**が担当する（旧仕様の「経理が完了後に差戻し」は廃止）。経理（admin_master）による差戻し遷移は定義されていない。

### 3.5 再提出・事務の処理

```
returned_to_tutor  --submit(講師)-->  awaiting_school
returned_to_office --submit(事務)-->  awaiting_sales
returned_to_office --approve(事務)--> awaiting_sales（事務が前進）
returned_to_office --return(事務)-->  returned_to_tutor（事務が講師へ差戻し）
```

> 営業からの差戻しは事務（office）が受け持つ。事務は「承認＝営業へ前進」「差戻し＝講師へ」のいずれかを選ぶ。

> 講師の承認管理・報告書一覧では、直近の差戻しイベント（差戻し要求の許可を含む）の遷移元に応じて差戻し元を「学校」または「運営」と表示する。学校承認・事前確認・最終承認の日時は、差戻し後に再承認された場合も各工程の直近の承認イベント日時を表示する。

### 3.6 講師起点の差戻し要求（2026-07-10 追加）

講師は提出後の報告書について、**現在ボールを持っているロール**へ差戻しを要求できる。差戻しの実行権限は従来どおり承認者側にあり、講師は「要求」のみを行う。

| アクション | 実行者 | 遷移 | コメント |
|-----------|--------|------|---------|
| `request_return`（差戻し要求） | tutor（本人） | ステータス不変（イベント記録のみ） | **必須**（要求理由） |
| `approve_return_request`（要求許可） | ボール保持ロール | 対象ステータス → `returned_to_tutor` | 任意（要求理由を自動転記） |
| `decline_return_request`（要求却下） | ボール保持ロール | ステータス不変 | **必須**（却下理由。講師に表示） |

対象ステータスとボール保持ロール（`definitions.RETURN_REQUEST_BALL_HOLDERS`）:

| 対象ステータス | ボール保持ロール（許可・却下できるロール） |
|---------------|------------------------------------------|
| `awaiting_office_precheck` / `awaiting_office` / `returned_to_office` | office |
| `awaiting_school` | school |
| `awaiting_sales` / `approved`（完了後） | sales |

ルール:

- **要求の引き継ぎ**: 要求が未解決のまま承認等でボールが移った場合、要求は**新しいボール保持ロールへ引き継がれる**（例: 学校確認待ちで要求→学校が承認→事務が要求に対応する。完了まで進めば営業が対応する）。
- **要求の解決条件**: ①許可（講師へ差戻し） ②却下 ③通常の差戻しで講師に報告書が戻る（`returned_to_tutor` への遷移） ④クローズ、のいずれかのみ。却下後は講師が再要求できる。
- **導出方式**: 要求の未解決状態はDBカラムではなく `work_report_events` の履歴から導出する（`WorkReport.return_request_pending` / `return_request_comment` / `return_request_declined_comment`。`ReportOut` で返却）。**DBマイグレーション不要**。
- 許可イベントのコメントには講師の要求理由が「【講師の差戻し要求】…」として自動転記され、講師画面の差戻し理由・タイムラインが単体で読める。許可による差戻しは通常差戻しと同じ扱い（`last_return_comment` / `last_return_actor_role` に反映、差戻し元は営業許可の場合「運営」表示）。
- **メール通知は行わない**（設計判断）。承認者への到達経路は、事務・営業＝ダッシュボードの「あなたのタスク」（要求対応の行として表示・KPI件数にも算入）とパイプラインの「差戻し要求」バッジ、学校＝承認管理一覧のバッジ・案内。実際の許可・却下は参照画面（report_view）の要求パネルで行う。
- 職務分掌（§2）は要求の許可・却下にも適用される（承認・差戻しと同じ工程判断のため）。
- 学校がボールを持つ要求（`awaiting_school`）は、学校の承認操作が当月限定のため**当月のみ**講師画面に要求ボタンを表示する（他のステータスは過去月でも要求可）。
- 二重要求は不可（未解決の要求がある間は再要求できない）。要求・却下は `submitted_at` や各承認日時に影響しない。

---

## 4. 報告書ステータス一覧

| 値 | 日本語名 | 承認担当 | 終端 |
|----|---------|----------|:----:|
| `draft` | 下書き | tutor | |
| `awaiting_office_precheck` | 事務事前確認待ち（月分超過・1〜9分手入力時） | office | |
| `awaiting_school` | 学校承認待ち | school | |
| `awaiting_office` | 事務確認待ち | office | |
| `awaiting_sales` | 営業確認待ち（＝最終承認） | sales | |
| `approved` | 最終承認済み（完了） | — | ✓ |
| `returned_to_tutor` | 講師へ差戻し | tutor | |
| `returned_to_office` | 事務へ差戻し | office | |
| `closed` | クローズ（強制終了） | — | ✓ |

> `WorkStatus` の全列挙値は `new_backend/app/workflow/definitions.py` の `WorkStatus.ALL` を参照。`awaiting_finance`（経理確認待ち）の値自体は列挙に残るが、新しい通常フローでは使用されない（営業承認で完了するため）。

各遷移は `work_report_events` に監査ログとして記録される（action / from_status / to_status / comment / actor）。報告書の現在の承認担当は `work_reports.current_approver_role` に保持する。事務修正は `office_edit`、差戻し中の講師修正は `tutor_edit` として同テーブルに記録される。

---

## 5. 画面一覧

### HTML ページ（pages router: `new_backend/app/api/pages.py`）

| パス | テンプレート | 対象ロール | 概要 |
|------|-------------|-----------|------|
| `GET /` | （ロール別リダイレクト） | 認証済み | ロールに応じた初期画面へ（admin_master/admin_chief は `/finance/queue`） |
| `GET /login` | login.html | 未認証 | ログイン |
| `GET /select-role` | select_role.html | 認証済み（複数ロール） | 使用ロール選択 |
| `GET /change-password` | change_password.html | 認証済み | パスワード変更（初回強制変更の誘導先＋セルフサービス） |
| `GET /register` | register.html | 未認証 | 招待トークンからの登録 |
| `GET /forgot-password` / `GET /reset-password` | forgot_password.html / reset_password.html | 未認証 | パスワードリセット |
| `GET /tutor/reports` | tutor/reports.html | tutor | 報告書一覧・作成（業務連絡表） |
| `GET /tutor/reports/new` | tutor/reports.html | tutor | 新規作成（フォームへスクロール） |
| `GET /tutor/reports/{id}` | tutor/report_detail.html | tutor | 報告書詳細 |
| `GET /tutor/approval` | tutor/approval.html | tutor | 承認管理（提出・再依頼・差戻し確認） |
| `GET /tutor/submit` | （/tutor/reports へ 301） | tutor | — |
| `GET /school/approval` | school/approval.html | school | 学校承認（講師×月のカード） |
| `GET /school/reports` | （/school/approval へ 301） | school | — |
| `GET /office/queue` | office/queue.html | office | 事務キュー（タスク・パイプライン） |
| `GET /sales/queue` | sales/queue.html | sales | 営業キュー |
| `GET /finance/queue` | finance/queue.html | admin_master / admin_chief | 経理キュー（管理者用パイプライン） |
| `GET /admin/users` | admin/users.html | admin_master / admin_chief / sales / office | ユーザー管理（招待統合、学校詳細で学校承認スキップ設定） |
| `GET /admin/contracts` | admin/contracts.html | admin_master / admin_chief / sales / office | 契約管理（CSV一括登録対応） |
| `GET /admin/reports/{id}` | admin/report_detail.html | sales / office / admin_master / admin_chief | 報告書詳細（管理側） |
| `GET /admin/stale-reports` | admin/stale_reports.html | admin_master / admin_chief / sales / office | 未処理報告一覧 |
| `GET /reports/{id}/view` | report_view.html | 認証済み（全ロール） | 読み取り専用の報告書ビュー（別ウィンドウ） |

> 学校の承認管理には、講師が承認依頼を行った後の報告書のみ表示する。未提出の `draft` は一覧に含めず、担当校であっても詳細・履歴・PDF・チャットへ直接アクセスできない。提出後は学校承認後や差戻し中も履歴確認のため参照できる。他校の報告書はステータスにかかわらず参照・操作できない。

> `GET /admin/dashboard` は**廃止**（各ロールのダッシュボードと重複のため `/` へ 301 リダイレクト）。
> 事務・営業・経理の3キュー（office/sales/finance/queue.html）はほぼ同一構造。表示ロジック変更時は各ファイルへ反映する。

---

## 6. API 仕様

すべて `/api/w` プレフィックス（認証のみ `/api/auth` を指導実績報告システムと共有）。認可は各エンドポイントの `require_role(...)` に従う。

### 認証 API（共有）

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/auth/login` | 不要 | ログイン |
| POST | `/api/auth/select-role` | ログイン済み | 複数ロール時の使用ロール選択 |
| POST | `/api/auth/logout` | ログイン済み | ログアウト |
| GET | `/api/auth/me` | ログイン済み | 現在ユーザー情報 |
| GET/POST | `/api/auth/register` | 不要（token） | 招待情報取得 / 登録 |
| POST | `/api/auth/forgot-password` | 不要 | リセットメール送信 |
| GET/POST | `/api/auth/reset-password` | 不要（token） | トークン確認 / パスワード設定 |

### 報告書 API（`new_backend/app/api/reports.py`）

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/w/reports` | tutor | 報告書作成（契約未登録の紐付けは 409） |
| GET | `/api/w/reports` | 認証済み | 一覧（ロール別フィルタ。事務・営業・経理・管理責任者は全件） |
| GET | `/api/w/reports/monthly-summary` | tutor / admin_master / admin_chief | 月別サマリー |
| GET | `/api/w/reports/admin-separation-locks` | office / sales / admin_master / admin_chief | 職務分掌のUI制御用。兼務スタッフが事務／営業承認済みの講師ID一覧 |
| POST | `/api/w/reports/bulk-action` | 認証済み（actor_role で判定） | 複数報告書への一括アクション（submit/approve/return/skip_school） |
| GET | `/api/w/reports/export` | 認証済み | PDF / CSV 一括エクスポート（`format=pdf\|csv`） |
| GET | `/api/w/reports/{id}` | 認証済み | 詳細取得 |
| PATCH | `/api/w/reports/{id}` | tutor | 本人による編集（下書き／差戻し中。契約由来メタは固定。差戻し中の保存は差分通知） |
| PATCH | `/api/w/reports/{id}/office-edit` | office | 事務による報告書修正（事務確認待ち・営業確認待ち・事務差戻し中。再承認不要、差分を講師・学校へ通知。勤怠区分の取得回数・欠勤回数管理にも使用） |
| DELETE | `/api/w/reports/{id}` | tutor | 削除（draft / returned_to_tutor の本人分のみ） |
| POST | `/api/w/reports/{id}/action` | 認証済み（actor_role で判定） | ワークフロー遷移（submit/approve/return/skip_school/close/request_return/approve_return_request/decline_return_request） |
| POST | `/api/w/reports/{id}/close` | 認証済み（sales / office / admin_master / admin_chief） | クローズ（理由必須・当月不可・終端不可） |
| GET | `/api/w/reports/{id}/events` | 認証済み | イベント履歴 |
| GET | `/api/w/reports/{id}/export` | 認証済み | 単一 PDF 出力 |
| GET | `/api/w/stale-count` / `/api/w/stale-reports` | 認証済み（一覧は sales / office / admin_master / admin_chief） | 未処理報告 件数 / 一覧 |

> 報告書の編集ができるのは**本人（講師）＝ `PATCH /{id}` と 事務 ＝ `PATCH /{id}/office-edit` のみ**。営業は承認／差戻しのみで編集はできない。
> `POST /{id}/action` と `POST /bulk-action` のロール許可は `_ACTION_ALLOWED_ROLES`（submit / request_return = tutor、approve / return / approve_return_request / decline_return_request = school/sales/office/admin_master/admin_chief、skip_school=admin_chief）と、実際の遷移可否（`definitions.find_transition`。差戻し要求の許可・却下はボール保持ロールのみ）の両方で制御される。

### ユーザー API（`new_backend/app/api/users.py`）

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| GET | `/api/w/users/me` | 認証済み | 現在ユーザー |
| GET | `/api/w/users` | 認証済み（一覧は admin_master、role フィルタ可） | ユーザー一覧 |
| GET | `/api/w/users/export` | admin_master | ユーザー CSV エクスポート |
| POST | `/api/w/users/import` | admin_master | ユーザー CSV インポート |
| PATCH | `/api/w/users/{id}` | admin_master | 更新（`skip_parent_approval`＝学校承認スキップを含む） |
| PATCH | `/api/w/users/{id}/roles` | admin_master | ロール更新（営業・事務） |
| PATCH | `/api/w/users/{id}/disable` / `/enable` | admin_master | 無効化 / 有効化 |
| DELETE | `/api/w/users/{id}` | admin_master | 論理削除 |
| POST | `/api/w/users/{id}/reset-password` | admin_master | パスワード初期化 |

### 契約・紐付け・招待・チャット API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| GET / POST | `/api/w/contracts` | admin_master | 契約一覧 / 作成 |
| GET | `/api/w/contracts/export` | admin_master | 契約 CSV エクスポート / テンプレート DL |
| POST | `/api/w/contracts/import` | admin_master | CSV 一括登録（multipart、UTF-8/Shift-JIS 自動判定） |
| GET | `/api/w/contracts/for-tutor` | tutor | 自分の契約＋動的列定義 |
| GET / PATCH / DELETE | `/api/w/contracts/{id}` | admin_master | 詳細 / 更新 / 論理削除 |
| POST / GET | `/api/w/admin/profiles` | admin_master（GET は sales も）/ office | プロファイル作成 / 一覧 |
| PATCH | `/api/w/admin/profiles/{id}` | admin_master / office | プロファイル更新 |
| POST | `/api/w/assignments` | admin_master | 紐付け作成 |
| POST | `/api/w/assignments/for-school` | tutor | (講師×学校) 紐付けの取得／作成 |
| GET | `/api/w/assignments` | 認証済み（講師は自分のみ） | 一覧 |
| PATCH / DELETE | `/api/w/assignments/{id}` | 認証済み / admin_master | 編集（リマインド設定等。学校承認スキップは `/api/w/users` へ移設）/ 削除（報告書なし時） |
| POST / GET / DELETE | `/api/w/invitations` | admin_master | 招待 作成・再送 / 一覧 / 削除 |
| GET / POST | `/api/w/reports/{id}/messages` | 認証済み | チャット一覧 / 投稿 |
| POST | `/api/w/reports/{id}/messages/{msg_id}/read` | 認証済み | 既読登録 |

> 契約管理（`GET/POST /api/w/contracts` 等）の認可は API レベルでは `admin_master`。画面（`/admin/contracts`）は sales/office/admin_chief にも開放されているため、運用上の権限境界は画面と API で差がある点に注意。

---

## 7. データモデル（概要）

詳細スキーマ（カラム・型・制約・`form_data` JSONB 構造）は `../DATA_MODEL.md §3` を**唯一の参照先**とする。本書では構成のみを示す。新システム専用テーブルは `work_` プレフィックスを持つ。

| テーブル | 役割 |
|----------|------|
| `work_assignment_profiles` | 契約マスタ 兼 フォーム設定。(講師, 学校) ごと1件、`assignment` と 1:1（`UNIQUE(tutor_id, school_id)`／`assignment_id` UNIQUE） |
| `work_reports` | 業務連絡表。紐付け×月で1件（`UNIQUE(assignment_id, target_month)`）。明細・ヘッダーは `form_data`(JSONB) |
| `work_report_events` | ワークフロー操作・修正の監査ログ（approve/return/submit/skip_school/close/office_edit/tutor_edit/request_return/approve_return_request/decline_return_request） |
| `work_chat_messages` / `work_chat_reads` | 報告書チャット・既読管理 |
| `work_notifications` | アプリ内通知ログ |
| `work_mail_outbox` | 実メール配信の送信待ちキュー（1通ずつ間隔をあけて送信。`work_notifications` とは別） |

- 共有テーブル（`users` / `assignments` / `invitations`）は新システム用に `users.user_no` / `users.allowed_systems` / `assignments.system_type` 等を追加。
- マイグレーションは `new_backend/migrations/`、バージョンテーブルは `work_alembic_version`（指導実績報告システムの `alembic_version` と分離）。新システムコンテナは起動時に `alembic upgrade head` を実行。
- `work_reports.form_data` の `meta.column_definition` に作成時の動的列定義をスナップショットし、保存後は契約変更の影響を受けない（report_view・PDF・CSV が共通で参照する唯一の列定義源）。

> 注記: `../DATA_MODEL.md` の §3 のスキーマ（カラム・型・JSONB 構造）は正確だが、同書の「WorkStatus 列挙値」「状態遷移サマリー」表は旧フロー（経理が `awaiting_finance → approved` で最終承認、`skip_school` を sales/office/admin_master が実行）の記述が残っており、本書 §3・§4 およびコード（`definitions.py`）が正である。

---

## 8. 契約管理機能

経理（admin_master）が `/admin/contracts` 画面で管理する（画面は sales/office/admin_chief にも開放）。契約 = (講師, 学校) ごと1件で、対応する `assignment` を自動解決／作成する。

### 契約の項目

- 基本: お客様ID（`customer_id`）・弊社担当（`our_staff`）・派遣先事業所の所在地・就業場所（`work_location`・報告書の「事業所の所在地」の下に表示）・教室名・契約期間（開始／終了）・月時間（分）／週コマ数の期間付き複数ケース（`workload_cases`）・シフト備考・従事業務内容（`work_content`）
- 報告書フォームの項目表示フラグ: `show_dispatch_address` / `show_work_content` / `show_commuter_pass` / `show_break_minutes` / `show_schedule_note`（契約からライブ反映）
- 担当業務①〜③（`task_name_1..3`・①必須）／副業務①〜⑤（`sub_task_name_1..5`・任意）: 業務名・委託業務ID（`task_id` / `sub_task_id`）・個別契約ID（`contract_id` / `sub_contract_id`）。業務名があるもののみ報告書に「業務名（分）」列を生成
- 採点欄: `採点を追加する`（`scoring_enabled`）＋ 項目名（`scoring_label`・既定「採点」）・単位（`scoring_unit`・既定「回」）・委託業務ID・個別契約ID（有効時のみ報告書末尾に「採点（回）」列を生成）

### CSV 一括登録

| 機能 | エンドポイント | 仕様 |
|------|---------------|------|
| テンプレート DL | `GET /api/w/contracts/export`（テンプレート） | UTF-8(BOM付)。ヘッダー＋記入例1行 |
| インポート | `POST /api/w/contracts/import`（multipart） | 文字コード UTF-8 / Shift-JIS 自動判定 |

- 識別子: 講師＝講師番号（`user_no` または `tutor_no`）、学校＝学校名（`display_name`）。
- 重複（講師×学校）は **upsert**（既存上書き）。
- 検証は **全件成功か全件中止**（1件でもエラーなら行番号付きでエラー一覧を返し全件ロールバック）。
- 記入例・コメント行（講師番号が空 or 先頭 `#`）と空行はスキップ。

---

## 9. 報告書フォーム（動的列定義）

報告書（業務連絡表）の明細列は、契約（`work_assignment_profiles`）から `services/contract_form_service.build_column_definition()` で動的生成する。

### 列構成（左 → 右）

| 区分 | 列 | データキー | 種別 |
|------|----|-----------|------|
| 固定（先頭） | 日付 | date | date |
| 固定（先頭） | 業務開始時間 | start | time |
| 固定（先頭） | 業務終了時間 | end | time |
| 固定（先頭） | 担当時限 | subject_period | number |
| 動的 | 担当業務①〜③（登録分のみ）「業務名（分）」 | task_minutes_1..3 | number（合計対象） |
| 動的 | 副業務①〜⑤（登録分のみ）「業務名（分）」 | sub_minutes_1..5 | number（合計対象） |
| 動的 | 採点（scoring_enabled時のみ）「採点（回）」 | scoring_count / scoring_minutes | count_minutes（1セルに 回／分 を併記） |
| 固定（末尾） | 休憩時間（分） | break_minutes | number（合計対象） |
| 固定（末尾） | 往復交通費（円） | commute_fee | number（合計対象） |
| 固定（末尾） | 内容 | note | text |

- 委託業務はメイン業務（担当業務①〜③、上限3・①必須）とサブ業務（副業務①〜⑤、上限5・任意）の2区分。列はメイン→サブの順に生成される。委託業務は常に「分のみ」、採点のみ「回＋分」併記。
- 「回数」「曜日」列はフロント側が自動生成するためデータ列には含めない。
- **種別（kind）列**: 日付の直後に「種別」列（勤務／有給／欠勤／自己都合／学校行事）が差し込まれる。これはスナップショット列定義（`meta.column_definition`）には含まれず、講師フォーム・参照ビュー（report_view）・PDF・CSV が共通で日付の直後に挿入する（`export_service._KIND_COLUMN`、`line.kind` ＝ `paid_leave` / `absent` / `personal_reason` / `school_event` / 勤務）。
  - **有給・欠勤**: 行の業務入力をすべて無効化・クリアする（勤務時間を持たない）。月内の取得回数・欠勤回数として集計。
  - **自己都合・学校行事**: 担当時限＝選択不可、担当業務（分）＝0固定。副業務・採点・休憩・交通費・内容は手動入力可。勤務日数には含めず「自己都合：N回・学校行事：N回」として集計するが、入力した副業務等の分は各列の合計に含める。

### 入力の自動計算（講師フォーム・事務修正モーダル共通）

講師の入力UI（`templates/tutor/reports.html`）と事務の報告書修正モーダル（`templates/office/queue.html`）はクライアント側で以下を自動計算する。計算・種別・労基休憩などのルールは**共通コア `static/js/work_report_calc.js`（`window.WorkReportCalc`）** に集約しており、両画面（＋講師のスマホ詳細シート）が同一実装を共有する（**入力仕様の変更は必ず共通コア側で行い、片側だけの修正は禁止**）。サーバは `form_data` をそのまま保存する（値の形式は従来と同じ `start` / `end`（HH:MM）・各分数のため、report_view・PDF・CSV に影響しない）。

- **担当時限 → 担当業務（分）**: 選択したコマ数 × 50分 を「担当時限の右隣」の担当業務（分）列へ自動入力する。
- **担当時限 → 休憩時間（分）**: （コマ数 − 1）× 10分 を休憩時間（分）へ自動入力する（1コマは0分。契約で休憩列非表示の場合はスキップ）。
- **業務開始〜終了時間**: 手動入力不可（自動計算のみ）。開始は **8:40固定**、終了は「担当時限より右の分数列（担当業務・副担当業務・採点の分・休憩時間）の合計」を開始に加算した時刻。往復交通費（円）・採点の回数は加算しない。分数列がすべて空（合計0）の行は時間を持たない。
- 自動入力された各分数は **1分単位で手動修正**でき、修正に業務開始〜終了時間が連動する。担当時限を選び直すと担当業務（分）・休憩時間（分）は自動値で上書きされる。
- 合計が同日23:59を超える場合は「計算不可」を表示し、保存をブロックする。
- 有給・欠勤（`kind`）の行は時間を持たない（自動計算対象外）。自己都合・学校行事の行は担当業務（分）が0固定のため、手動入力した副業務等の分数の合計から業務開始〜終了時間を自動計算する。
- 日付入力ボックス内の右側に曜日「(火)」を併記する（例: `2026/07/07 (火)`。ボックス幅は124px固定でカレンダーアイコンは非表示、クリックでピッカーが開く）。
- 編集可能な報告書（下書き・差戻し）を開いた時点で業務開始〜終了時間は自動計算値へ正規化される。読取専用表示は保存値のまま。

#### 事務の報告書修正モーダル（office/queue.html）

事務ダッシュボード（進捗パイプライン）の「編集」から開く修正モーダルは、講師フォームと**同一の入力仕様**で動作する（2026-07-10改修。従来は全セル手動入力の旧仕様だった）。

- 列は報告書の保存済み列定義（snapshot）を使用し、休憩時間列は契約の `show_break_minutes` フラグでライブに出し分け（講師フォームと同一）。種別（kind）列は日付の直後へ差し込む。
- 種別による活殺（有給/欠勤=業務セルをクリアして無効化、自己都合/学校行事=担当時限選択不可＋担当業務0固定）・行の背景色・担当時限の複数選択ポップオーバー（コマ設定契約は時間帯つき①〜、未設定は1〜10）・担当業務/休憩の自動入力・業務開始〜終了時間の自動計算（開いた時点で正規化）・同日重複ガード・労基休憩の下限をすべて共有する。
- コマ設定（`period_slots`）は `GET /api/w/contracts`（office権限）から講師×学校で該当契約を引いて参照する（モーダル初回表示時に一度だけ取得）。契約が無い報告書は8:40固定ルール。
- 行数は講師フォームと同じ26行まで空行を表示し、日付の追加修正ができる。モーダル下部に集計（勤務日数・種別回数・summable列の合計）を表示する。
- 保存時の検証（1行以上・種別行の日付必須・担当時限1〜10/コマ範囲・労基休憩・23:59超過・同日重複）は講師フォームの保存時と同一ルール・同一メッセージ。数値の空欄は空のまま保存する（0埋めしない＝未記入行判定と互換）。
- 保存先APIは従来どおり `PATCH /api/w/reports/{id}/office-edit`（サーバ側は変更なし）。

### 記入コピー（講師フォーム）

報告書一覧の上部（フォーム外の右端）に2つのコピーボタンを並べて表示する。いずれも編集可能な報告書（当月の下書き・差戻し）でのみ有効（読取専用時は無効化、提出専用ページでは非表示）。

- **前回の記入分をコピー**: 最後に記入した行と同じ内容（日付以外）を次の空き行へ複製する。スマホではコピー先の行の詳細シートを自動で開く。
- **先月の記入分をコピー**（2026-07-08）: 選択中の学校の**先月の報告書**の明細（種別を含む・ステータス不問）を当月フォームへ反映する。
  - 日付は「**同じ第N曜日**」で当月の日付へ変換する（例: 6月の第1水曜 06/03 → 7月の第1水曜 07/01）。変換後は日付昇順に並べ替える。
  - 当月に同じ第N曜日が存在しない行（第5週など）は日付を**空欄のままコピー**し、「N件の行は…日付を空欄にしています」と画面メッセージで通知する（明細リスト（スマホ）では「日付未設定」表示）。
  - 業務開始〜終了時間はコピーせず、コピーした分数から自動再計算する（上記「入力の自動計算」と同一ルール）。
  - メタ項目（要望連絡事項・定期代等）はコピー対象外。既に入力中の明細がある場合は置き換えの確認ポップアップを表示する。
  - フォームへの反映のみで**サーバ保存はしない**（保存は従来どおり画面下の「保存/更新」）。先月の報告書が無い・明細が未記入の場合はエラーメッセージを表示する。

### スマホ入力UI（講師フォーム・画面幅768px未満）

講師の入力UI（`templates/tutor/reports.html`）は、画面幅 768px（Tailwind `md`）未満では横スクロールが必要な明細テーブルを表示せず、次の2画面で入力する（2026-07-08）。PC（md以上）は従来のテーブル入力のまま。事務修正画面・report_view・PDF・CSV は変更なし。

- **明細リスト（画面①）**: 記入済みの行だけを「日付・開始・終了・交通費・事由」で一覧表示する。日付はリンク表示（行全体タップ可）。事由には種別ラベル（有給／欠勤／自己都合／学校行事）を表示し、勤務の行は空欄。行の背景色は種別に応じてテーブルと同じ配色。「＋ 日付を追加」で最初の空き行の詳細シートを開く。
- **明細詳細シート（画面②）**: 1日分をまとめて入力するフルスクリーンシート（`#lineSheetOverlay`）。日付（曜日併記）→ 種別 → 担当時限（`①08:40〜09:30` 形式の時間帯つきトグル。時間帯は開始8:40＋50分授業＋10分休憩から導出）→ 担当業務 → 休憩時間 → 管理業務（副担当業務・採点）→ 業務開始〜終了時間（自動計算・読取専用）→ 往復交通費 → 内容 の順。自動計算・種別による活殺・同日重複ガード・種別行の日付必須は明細行と同一ルール（`computeAutoTimes` / `periodAutoFillValues` 等の共通関数を使用）。
- シートの「保存」は**フォームの該当行へ反映して閉じる**（画面①へ戻る）。サーバへの保存は従来どおり画面下の「保存/更新」ボタンで行う（スマホでは画面下部へ吸着表示し、常に押せる）。
- 実装は明細テーブルの行入力を唯一のデータ源とし、シートは開くときに行から値・活殺を読み、保存で行へ書き戻す。保存・集計・提出判定（1〜9分手入力の事前確認フロー判定を含む）はPC表示と完全に共通。
- 読取専用の報告書（過去月・承認済み等）は行タップで参照のみ（シートの保存ボタン非表示）。「前回の記入分をコピー」はスマホではコピー先行の詳細シートを自動で開く。

### デフォルトフォーム（契約未設定時）

`forms/definitions.py` の `monthly_dispatch`（月次派遣報告）。動的列スナップショットを持たない旧データの救済用フォールバックとしても用いる。

> 補足（PDF / 参照ビューの列整合）: 読み取り専用ビュー（`report_view.html`）と PDF エクスポート（`services/export_service.py`）は、各報告書に保存されたスナップショット列定義 `form_data.meta.column_definition`（`_snapshot_columns`）を参照する。したがって**契約由来の動的列（担当業務・副業務・採点）は report_view・PDF にも反映され、両者の列・値・集計は一致する**。PDF は動的列で横に広くなるため **A4 横向き**で出力する。スナップショットの無い旧データのみ静的フォーム定義（`monthly_dispatch`）へフォールバックする。
>
> （旧仕様書にあった「report_view / PDF は静的 `monthly_dispatch` を用いるため動的列が反映されない」という制約は誤りであり、削除した。）
>
> 補足（PDF / 参照ビューの項目網羅）: 参照ビュー（report_view）と PDF は、講師フォームの**全項目**を漏れなく表示する（2026-07-09）。基本情報＝事業所の名称・組織単位／教室名／事業所の所在地／就業場所（2026-07-16 追加。所在地の直下・左列のみ）／氏名／講師番号／お客様ID／従事業務内容（2列グリッド・左＝事業所／右＝講師）、明細＋勤怠サマリの下に＝弊社担当／委託業務（契約より。列定義スナップショットから担当→副→採点の順に導出）／スケジュール欄／要望連絡事項／定期代セクション（期間選択・金額／区間／購入日／期間from〜to。全項目未記入は「記入なし」）。未入力のメタ項目は「-」表示。

---

## 10. 通知仕様

`services/notification_service.py` がワークフロー遷移・スケジューラに応じて `work_notifications` レコード（アプリ内通知ログ）を作成し、実メールは送信キュー（`work_mail_outbox`）へ投函する。実送信はバックグラウンドのドレイナ（`services/mailer.drain_outbox`）が1通ずつ間隔をあけて行う（バースト対策）。

### 通知種別（ワークフロー遷移）

| 通知種別 | テンプレート | 宛先 | トリガー |
|----------|-------------|------|---------|
| approval_request | notify_approval_request.txt / notify_submitted_to_admin.txt | school / office / sales | submit・approve で次の承認待ちへ（提出が事務確認待ち／事前確認待ちへ向かう場合は事務へ） |
| approved_by_school | notify_parent_approved.txt | tutor | 学校が承認（awaiting_school → awaiting_office） |
| final_approved | notify_admin_approved.txt | tutor / school | **営業承認による最終承認（awaiting_sales → approved）** |
| returned | notify_returned.txt | tutor / office | return 実行（差戻し先へ） |
| office_edit | notify_office_edited.txt | tutor / school | 事務が報告書を修正（office-edit）。宛先は常に講師・学校（学校承認スキップ校にも送る・2026-07-10改修）。本文は担当者の個人名を出さず「下記の業務連絡表がイスト事務担当者により修正されました。」表記（コメントの差出人も「イスト事務担当者からの連絡：」） |
| tutor_edit | notify_tutor_edited.txt | 直近に差戻した操作者 | 差戻し中の報告書を講師が修正・保存 |
| reminder_unapproved / reminder_unsubmitted / reminder_school_approval / stale_report_{level} | （記録のみ） | school / tutor / sales / office / admin_master 等 | スケジューラ（後述） |

> 完了通知（final_approved）は営業承認時に講師・学校へ送られる。経理（admin_master）宛の最終承認依頼通知は存在しない（経理は最終承認者ではないため）。

### スケジューラ（APScheduler / JST）

- **月末リマインダー**: 毎日 09:00。月末が近い未提出（draft / returned_to_tutor）・未承認（awaiting_school）報告へ通知。
- **未処理報告チェック（stale check）**: 毎日 06:00。一定期間滞留した報告に `stale_since` を設定しエスカレーション通知（sales / office / admin_master）。
- **学校承認リマインド**: 紐付け単位（`reminder_days_after` 間隔・`reminder_count` 回まで・JST 同日重複防止）。

### メールテンプレート一覧（`new_backend/app/templates/email/`）

invitation.txt / invitation_tutor.txt / invitation_staff.txt / notify_approval_request.txt / notify_submitted_to_admin.txt / notify_returned.txt / notify_admin_approved.txt / notify_parent_approved.txt / notify_office_edited.txt / notify_tutor_edited.txt / password_reset.txt / status_changed.txt

> 本文の宛先ラベルは **対象学校／学校名／担当学校**（`{student_name}` には EMPS では学校名が入る。DATA_MODEL の assignments.student_name 参照）。「生徒」表記は指導報告・指導時間確認票（legacy）側のステークホルダ呼称のため、EMPS のメールには使わない（2026-07-08 に 対象生徒→対象学校 等へ全テンプレート統一。`tests/test_mail_queue.py::test_email_templates_use_school_label_not_student` でガード）。
