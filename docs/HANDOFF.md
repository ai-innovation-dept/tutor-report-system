# 引継ぎ書 (HANDOFF)

別の担当者 / 別の Claude Code アカウントが作業を引き継ぐための文書。**まずこれを読み、次に `CLAUDE.md`・`docs/INFRASTRUCTURE.md` を読むこと。**

> メモ: Claude Code の個人メモリ（`~/.claude/...`）はアカウント/PCに紐づくため引き継がれない。引継ぎに必要な文脈はすべて本ファイル（リポジトリ）に集約している。

最終更新: 2026-07-21（EMPS: 講師の過去月編集を解禁＝承認フロー全体を月非依存化 202607211716・EMPS①＝案B／代々木進学会: 講師起点の差戻し要求 202607211144）

---

## 0. システム構成（最低限）

| | イスト勤怠レポート for 代々木進学会<br>（旧称: 指導実績報告システム / 既存=legacy） | イスト勤怠レポート for EMPS<br>（旧称: 業務連絡表システム / 新=new） |
|---|---|---|
| ディレクトリ | `backend/` | `new_backend/` |
| ポート | 8000 | 8001 |
| ロール | tutor / parent / admin_receiver / admin_reviewer / admin_master / admin_chief | tutor / school / sales / office / admin_master / admin_chief |

- **DB（Postgres `tutor`）・`users`/`assignments`/`invitations` テーブル・`.env` を両システムで共有**。新システム専用テーブルは `work_*`。
- Alembic は legacy=`alembic_version` / new=`work_alembic_version` で分離。`users` 等の共有テーブルは **legacy 側 Alembic が管理**。
- 詳細は `docs/README.md`（索引）、各システムの `docs/イスト勤怠レポート for .../SPECIFICATION.md`、共通の `docs/DATA_MODEL.md`・`docs/INFRASTRUCTURE.md`、`CLAUDE.md`。

---

## 1. 🔴 最優先の未対応タスク：本番メールが実配信されない

**症状:** 本番から招待メール等を送っても受信箱に届かない。

**原因（2点）:** ① 既定が **`MAIL_BACKEND=console`**（＝実送信せずログ出力のみ）。② SMTP も既定で **MailHog のまま**（`host=mailhog / port=1025 / 認証なし`）。送信機構自体は実装済み（**SMTP認証＋TLS＋送信キュー**＝アウトボックスに投函し1通ずつ間隔送信。経路は両システム共通、送信元のみ `SMTP_FROM`/`NEW_SMTP_FROM` で分離）。**実配信には `MAIL_BACKEND=smtp` かつ 実在SMTP の両方**が必要。

**現状確認コマンド（本番）:**
```bash
sudo docker compose exec backend python -c "from app.config import settings as s; print('mail_backend=', s.mail_backend, '| smtp=', s.smtp_host, s.smtp_port, s.smtp_tls, s.smtp_from)"
```
`mail_backend=console` または `host=mailhog` なら未配信が確定。

**対処A（最短）— `mailmode.sh` で切替:** `.env` に `MAIL_LIVE_*`（Brevo等の実配信SMTP）を設定済みなら、`sudo bash mailmode.sh live` だけで `MAIL_BACKEND=smtp`＋SMTP差替＋再起動まで完了する（検証は `sandbox`＝Mailtrapで捕捉、停止は `off`）。

**対処B（手動）— 本番 `.env` に実 SMTP を設定 — 例は Gmail:**
1. Google アカウント（kintaikanri.tutor1@gmail.com）で 2 段階認証を有効化 → **アプリ パスワード 16 桁**を発行。
2. 本番 `~/tutor-report-system/.env` を編集:
   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=kintaikanri.tutor1@gmail.com
   SMTP_PASSWORD=（アプリパスワード16桁・スペース無し）
   SMTP_TLS=starttls
   SMTP_FROM=kintaikanri.tutor1@gmail.com
   NEW_SMTP_FROM=kintaikanri.tutor1@gmail.com
   MAIL_BACKEND=smtp
   ENVIRONMENT=production
   ```
3. 反映＆疎通確認:
   ```bash
   cd ~/tutor-report-system && git pull && sudo docker compose up -d --build
   sudo docker compose exec backend python -m app.scripts.send_test_email kintaikanri.tutor1@gmail.com
   ```
   `OK:` と出て Gmail（迷惑メールも）に届けば成功。

**対処（本番運用向け）— AWS SES:**
SES でも同じコードで動く（SMTP 認証＋TLS は実装済み）。本番 `.env` を次のように設定する:
```env
SMTP_HOST=email-smtp.ap-northeast-1.amazonaws.com   # SESのリージョン別SMTPエンドポイント（東京=ap-northeast-1）
SMTP_PORT=587
SMTP_USERNAME=（SESコンソールで発行したSMTPユーザー名）
SMTP_PASSWORD=（SESコンソールで発行したSMTPパスワード）
SMTP_TLS=starttls                                   # 465を使うなら ssl
SMTP_FROM=no-reply@（SESで検証済みのドメイン/アドレス）
NEW_SMTP_FROM=no-reply@（SESで検証済みのドメイン/アドレス）
MAIL_BACKEND=smtp
ENVIRONMENT=production
```
SES 固有の注意:
- **SMTP 認証情報は SES 専用**（コンソールの「SMTP 設定 → SMTP 認証情報の作成」で発行。IAM のアクセスキー/シークレットとは別物）。
- **送信元(From)は SES で検証済み（verified identity）** のドメイン/アドレスにする（ドメイン検証＋DKIM 設定で到達率が上がる）。
- **サンドボックス状態では「検証済みの宛先」にしか送れない**。任意の宛先（kintaikanri.tutor1@gmail.com 等）へ送るには **本番アクセス申請**が必要（または当面はその受信アドレスも SES で検証しておく）。
- SMTP エンドポイントは**利用リージョンに一致**させる（東京なら `email-smtp.ap-northeast-1.amazonaws.com`）。

**注意（共通）:**
- 送信元(From)は**実在の認証済みドメイン/アドレス**にすること（`example.com`/`.local` は拒否・迷惑判定）。
- 送信量・到達率を重視するなら SES 等の専用サービス推奨。Gmail は約 500 通/日の上限。
- どの方式でも本番 `.env` を差し替えて `up -d --build` → `send_test_email` で確認するだけ（コード変更不要）。

---

## 2. 本番データのクリーン投入（サンプルユーザー）

- **`docker compose up -d --build` ではユーザーデータは消えない**（意図的）。本番を空にしてサンプルだけにするのは手動の別ステップ。
- 実行（破壊的・1回限り・初回セットアップ用。実ユーザー運用後は流さない）:
  ```bash
  sudo docker compose exec -T db pg_dump -U postgres -d tutor > backup_$(date +%Y%m%d_%H%M%S).sql  # 先にバックアップ
  sudo docker compose exec backend python -m app.scripts.seed_production --yes
  ```
- 手順詳細は `docs/INFRASTRUCTURE.md` セクション5。
- マイグレーション 0014 は `ENVIRONMENT=production` のとき `supervisor@example.com`（既知パスのテストユーザー）を投入しないガード付き。本番 `.env` に `ENVIRONMENT=production` を必ず設定すること。

### 検証用サンプルユーザー（全員 初期パスワード `Passw0rd!`・**単一ロール**）

| user_no | メール | 名前 | ロール | 所属 |
|---|---|---|---|---|
| 10001 | kintaikanri.tutor1@gmail.com | 講師太郎 | tutor | legacy + new |
| 40001 | kintaikanri.tutor1+school1@gmail.com | 保護者花子 | school | new |
| 50001 | kintaikanri.tutor1+office1@gmail.com | 受付太郎 | office | new |
| 50002 | kintaikanri.tutor1+sales1@gmail.com | 再鑑花子 | sales | new |
| 50003 | kintaikanri.tutor1+master1@gmail.com | 管理太郎 | admin_master | legacy + new |
| 90001 | kintaikanri.tutor1+supervisor@gmail.com | 管責花子 | admin_chief | legacy + new |

- Gmail のプラスエイリアスは全て **`kintaikanri.tutor1@gmail.com` の 1 受信箱**に届く（実在・到達するのでバウンスしない）。
- ⚠️ **各アカウントは必ず単一ロール**。当初 +school1/+office1/+sales1 に両システムのロールを併せ持たせたところ、ログインでロール選択画面が出て他システムのロールが選べず**ログイン不能**になった（`users.email` 一意＝1メール1行のため複数ロールが衝突）。単一ロールに修正済み（school/office/sales=new専用、tutor/master/chief=両システム）。**ここは元に戻さないこと。**
- 役割列の和名（保護者/受付/再鑑）と実ロール（school/office/sales）が一致しないのは、エイリアス名が新システムのシード名に一致するため new ロールで作成した経緯による。legacy 側で 保護者/受付/再鑑 のサンプルが必要なら別エイリアス（例 `+parent1`/`+receiver1`/`+reviewer1`）で追加する。

---

## 3. メール送信の設定（.env キー）

送信経路は**両システム共通**、送信元(From)のみ分離:

| キー | 用途 |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` | SMTP ホスト・ポート（共通） |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 認証（空なら認証なし）（共通） |
| `SMTP_TLS` | `none`(MailHog) / `starttls`(587) / `ssl`(465)（共通） |
| `SMTP_FROM` | 送信元（既存システム） |
| `NEW_SMTP_FROM` | 送信元（新システム） |
| `MAIL_BACKEND` | `smtp`=実送信 / `console`=ログのみ（**既定・実送信しない**） |
| `MAIL_SEND_INTERVAL_SECONDS` | 送信キューの1通ごと間隔（既定4秒。連打ロック回避） |
| `MAIL_SANDBOX_*` / `MAIL_LIVE_*` | `mailmode.sh sandbox/live` が読む 検証(Mailtrap)/実配信(Brevo等) の認証グループ |

- 実装: `backend/app/services/notification_service.py` と `new_backend/app/services/notification_service.py` の各 `_smtp_send_kwargs`。
- legacy の送信は失敗時に例外（招待APIは 500）。new の `send_email` は失敗を握りつぶしログ警告のみ。
- 設定例は `.env.example`（Gmail の具体例を含む）。

---

## 4. 運用・開発コマンド

```bash
# 本番デプロイ（手動・Lightsail SSH）
cd ~/tutor-report-system && git pull && sudo docker compose up -d --build
# ↑ マイグレーションは各コンテナ起動時に自動で alembic upgrade head 実行

# ローカル
docker compose up -d --build

# テスト
docker compose exec backend pytest
docker compose exec new_backend pytest

# メール疎通確認
docker compose exec backend python -m app.scripts.send_test_email <宛先メール>

# 本番クリーン投入（破壊的）
docker compose exec backend python -m app.scripts.seed_production --yes
```

---

## 5. 直近の主な改修

> 正確な履歴は `git log --oneline` を参照（手動コミット表は陳腐化しやすいため要点のみ）。
- **EMPS 講師の過去月編集を解禁＝承認フロー全体を月非依存化（改修依頼 202607211716・EMPS①＝案B）**（2026-07-21）: ルーズな講師が「月をまたいでから」業務連絡表を入力するケースを救済するため、従来「当月のみ操作可」だったフロントの月ゲートを撤廃し、講師の入力可否・承認側の操作可否をすべて**報告書のステータスで判定**（＝過去月も当月と同じ扱い）にした。**承認依頼前（下書き／差戻し／未作成）は当月・過去月を問わず編集可、提出後（承認待ち以降・承認済み）は読取専用**（修正は差戻し要求 202607211144 で対応）。EMPS はワークフロー遷移に元々月チェックが無い（すべてフロントのUI制限）ため**バックエンド・DB変更なし＝フロントのみ**。変更点＝①`tutor/reports.html`（「過去月は参照のみです」ゲート撤廃・「承認依頼」ボタンの当月限定条件撤廃・契約列は選択中の対象月で取得＝`for-tutor?target_month=selectedMonth`＋対象月変更時に再取得）②`report_view.html`（学校の承認・差戻し＝`schoolActionAreaHtml`と差戻し要求対応＝`canActOnRequest` の当月限定を撤廃）③`school/approval.html`（一覧の「過去月のため承認・差戻しはできません」案内を撤廃＝ステータス判定へ）④`tutor/approval.html`（過去月の `awaiting_school` を差戻し要求の対象に含める＝`requestableReports` の pastMonth 除外を削除）。**事務・営業は元々月非依存**（進捗パイプラインの対象月セレクトは全報告書月から生成・`report_view` の `staffActionAreaHtml` に月ゲート無し）のため変更不要。過去月の提出・承認では通常どおり通知メールが飛ぶ（意図どおり）。テスト: `tests/test_past_month_editing.py` 新設（過去月の 作成→編集(PATCH)→提出→学校→事務→営業 完走／提出後は 409 で編集不可／各画面のフロント月ゲート撤廃／`MAIL_BACKEND!=smtp` の実送信ゼロ確認＝8件）＝**new 486件通過**（実メール送信ゼロ＝`MAIL_BACKEND=console`）。**残（同一依頼 202607211716）: 代々木進学会（legacy①・過去月編集）と EMPS②（要望連絡事項の運営/講師/学校 3欄化）は次回**。**本番未反映**。
- **代々木進学会に「講師起点の差戻し要求」を実装（改修依頼 202607211144・EMPSからの移植）**（2026-07-21）: 提出後の報告書を講師が修正したいとき、承認管理から**差戻しを要求**（理由必須）でき、その時点で**ボールを持つ承認担当**が**許可（＝講師へ差戻し）／却下（理由必須）**する。ボール対応表＝`awaiting_parent_approval`→保護者（当月のみ）／`submitted_to_admin`・`returned_to_receiver`→受付／`received`・`re_reviewed`・`admin_approved`→再鑑（`workflow_service.RETURN_REQUEST_BALL_HOLDERS`）。**①保護者も対応者に含める・②最終承認済みからの許可は講師へ直接差戻す・③過去月も要求可（承認管理に対象月の切替を追加）**は依頼者確認済みの決定事項。要求の未解決判定は `report_events` からの導出（`return_request_state`）で **DBマイグレーション不要**、承認でボールが移っても要求は引き継がれる。許可時は要求理由を許可イベントのコメントへ自動転記（講師の差戻し理由・チャットへそのまま反映）。職務分掌（受付／再鑑の兼務不可）は許可・却下にも適用。**メールは要求・却下では送らず**（到達は講師カードのバッジ・運営ダッシュボードの「あなたのタスク」＋パイプラインのバッジ・保護者一覧の案内）、許可は従来どおり差戻しメールが講師へ届く。API は一括3本（`/api/reports/request-return-bulk`・`/approve-return-request-bulk`・`/decline-return-request-bulk`）。画面は `tutor/approval.html`（要求ボタン・要求中／却下の案内・対象月セレクトで過去月も表示）、`report_view.html`（許可・却下パネル＋対応できないロール向けの案内）、`admin/dashboard.html`（タスク行・KPI・パイプライン・履歴ラベル）、`parent/approval.html`（案内とバッジ）、`base.html`（理由入力の共通 `showPromptModal` を追加＝EMPSと同一実装）。テスト: `tests/test_return_request.py`（17件＝要求/許可/却下/引継ぎ/職務分掌/通知ゼロ）＋`tests/test_return_request_ui.py`（7件＝**サーバの対応表と3画面の複製が一致することを機械検証**）で **legacy 322件通過**。実メールは送らず（`MAIL_BACKEND=console`）、開発環境での実画面確認も要求→画面確認→イベント削除に留め、許可（＝メール送信）は実行していない。**本番未反映**。
- **ユーザー削除でメールアドレスを解放＋学校締め日のコピー＋legacyコピー機能＋CSVツールバー1行化（改修依頼 202607210807）**（2026-07-21）:
  - **② 削除したメールアドレスの再利用（両システム共通・仕様変更）**: ユーザー削除は従来どおり行を残す（過去の報告書・監査ログの参照整合性のため）が、**削除時に `users.email` を `deleted-xxxxxxxxxxxx@deleted.invalid` へ書き換えて解放**する（`backend/app/services/user_account_service.release_email_for_deletion` / `new_backend/app/services/user_service.release_email_for_deletion`＝**同一仕様の複製ペア・変更時は両方**）。これにより削除済みアドレスで**新規作成（招待）・コピー作成・CSV新規作成行**がすべて可能になる。あわせて**「削除済みアカウントの復活（revive）」を全面廃止**（CSV取込の revive 分岐と `revive_user`、招待受諾 `/api/auth/register` の復活分岐を削除）＝同じアドレスは**常に別アカウント**として作られる（データ保管の役割は無効で運用する方針）。CSV取込レスポンスの `revived` キーも撤去。既存の削除済みユーザーが握っているアドレスは **migration `0021_release_deleted_user_emails`（legacy alembic・users は共有テーブル）** で一括解放（元アドレスは保持しないため downgrade は no-op）。
  - **① EMPS: コピー新規登録で学校の締め日設定も複製**: 学校ロールをコピーすると `work_school_settings`（早期チェック・通知日数）と `work_school_deadlines`（登録済みの全年分の締め日）を複製する（`school_deadline_service.copy_school_settings`）。送信済みガード `notice_sent_at` は引き継がない（コピー先はこれから通知対象）。学校以外のロールでは設定行を作らない。DB変更なし。
  - **① 代々木進学会: ユーザーのコピー新規登録を追加**（EMPS 202607171557 と同一仕様）: `POST /api/users/copy`（`user_account_service.copy_user`）＋一覧の行「コピー」ボタン＋モーダル。氏名・メールのみ新規入力（どちらも重複は409）、ロール・利用システム・保護者承認スキップを複製、**担当（assignments）は引き継がない**、招待メールなしの直接作成（初期パスワード `Passw0rd!`・初回変更必須）、管理責任者のコピーは管理責任者のみ。
  - **③ EMPS: ユーザ管理のCSV操作バーを1行化**: 「CSVで一括登録・編集」と「学校の締め日設定CSV」の2段を**1行**へ統合。**対象セレクト（ユーザー／学校の締め日）**でエクスポート・インポートの動作を切り替え、対象年は「学校の締め日」選択時のみ表示（`#csvKind` / `#deadlineCsvYearField` / `#csvExportBtn` / `#csvImportBtn`＝ボタンは1組）。CSVヘルプの記載も追随。
  - テスト: legacy `tests/test_user_copy.py` 新設（コピー8件＝削除→同アドレス再作成を含む）、EMPS `tests/test_user_delete_email_release.py` 新設（解放・コピー再利用・招待再利用）＋`test_user_copy.py` に締め日複製2件。既存の「復活」テスト（legacy `test_invitations.py` 2件・EMPS `test_auth_users.py` 1件・両CSVテスト）は**別アカウント作成**の期待値へ更新。実メールは送らない（両conftestとも `MAIL_BACKEND=console`・送信キューに投函されるのみ）。e2e `new-user-csv.spec.js` は1行ツールバーの新セレクタへ更新。**本番未反映**。
- **代々木進学会 ユーザー管理テーブルをEMPS同構成へ最適化＋招待フォーム1行化（改修依頼 202607202109）**（2026-07-20）: 受付ロール以上の legacy ユーザー管理（`backend/app/templates/admin/users.html`）の「登録済みユーザー」テーブルと新規ユーザー登録フォームを、**EMPS（new_backend・202607171705＋202607201825）と同一の構成**へ最適化。①**ロール列**をチェックボックス方式（`min-w-72` で折返し・肥大）から**バッジ表示**（`roleBadges(userRoles(user))`）へ。②**行内「更新」ボタンを廃止**し、ロール変更（受付/再鑑等）は**「詳細」ドロワーの「ロール設定」へ集約**（ドロワーの `roleCheckboxes(user,'drawer')`＋保存は従来どおり＝受け皿は既存で維持）。③**状態・招待状態をバッジ**（有効=emerald／無効=slate／期限切れ=rose／未登録=amber・`invitationStatusBadge`）。④**操作ボタンを `row-actions` でコンパクト**（PCは小型・スマホのみ44pxタップ＝CSS `.mobile-cards td .row-actions button{min-height:44px}`）、セル余白 `px-3 py-2`＋`whitespace-nowrap`。⑤ロールタブ・CSVツールバー・検索窓もEMPSと同じコンパクト寸法。⑥**招待フォームを1行化**（改修依頼 202607201825と同じ＝ロール・氏名・メール・「招待メールを送る」を `grid items-end md:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)_auto]`・ボタン `h-[42px]`・保護者選択時の担当講師/生徒名は下段のまま維持）。**legacy固有は維持**＝ブランド色は blue（EMPSのemeraldにはしない）・parent ロール/担当講師・生徒名・admin_receiver/admin_reviewer・`/api/users`。状態バッジ/招待バッジ等の意味色（emerald/amber/rose）のみEMPSと共通。サーバ・API・他画面への影響なし（テンプレの表示のみ／`roleCheckboxes`・`updateUserRoles` 等のJSは drawer から継続利用）。テスト: `backend/tests/test_users_page_layout.py` 新設（バッジ化・行内更新撤去・row-actions・min-w-72撤去・招待1行・ドロワー受け皿）＝**legacy 290件通過**。Playwright で PC/モバイルの実画面＋詳細ドロワーのロール設定（6ロールのチェックボックス）表示を確認（表示のみ・保存/送信なし・DB非変更）。**本番未反映**。
- **EMPS 対象月セレクト拡張＋契約の有効化トグル＋コマ削除（改修依頼 202607201957・①②③④完了）**（2026-07-20）: ①**講師の対象月**（`tutor/reports.html`）: 「業務連絡表」の固定テキスト（セレクトの「〇月分」と重複）を削除し、対象月セレクトを `w-full`＋`sm:flex-1` で右へ引き伸ばし（xl=右カラム17remいっぱい・sm=トグル手前まで伸長）。②**契約の有効化トグル**（`admin/contracts.html`＋`api/contracts.py`）: 従来「無効化」（`DELETE hard=false`＝is_active False）しかできず戻せなかったため、**新規 `POST /api/w/contracts/{id}/activate`**（is_active True）を追加し、一覧の操作列を **状態に応じて「無効化」（有効時）↔「有効化」（無効時）** に出し分け（`enableContract`）。(講師,学校)は一意なので有効化で重複は起きない。③**コマ削除**（`admin/contracts.html`）: コマ設定（前期・後期の時間割）の各行に**削除✕ボタン**（`data-period-remove`）を追加。`removePeriodSlot(term,index)` は対象コマを削除して後続を前へ詰め直し（①からの連番を維持＝`collectPeriodSlots` の「途中に空きコマ不可」を満たす・最低1行は残す）。コマ設定未使用（グレイアウト）時は `applyPeriodSlotsUse` が region 内の全 `button` を disabled にするため削除ボタンも自動で無効。サーバ/DB変更は②のactivateエンドポイント追加のみ（migration不要）。他画面・legacy への影響なし。テスト: `test_contracts.py` に activate 1件＋`test_phase2_pages_and_bulk.py` に契約トグル/コマ削除・対象月の2件＝**new 473件通過**。Playwright で①対象月の伸長（xl/sm）②有効化↔無効化の出し分け③コマ追加→削除の詰め直し（4→5→4）を実画面で確認（表示・UI操作のみ／②の一時無効化はDB非変更のclient-side描画で確認・メール送信ゼロ）。④**契約管理の検索窓**（`admin/contracts.html`）: ユーザの指示で**ロール別タブは付けず、キーワード検索窓のみ**を追加（契約は「ロール」を持たないため）。テーブルカード上部に検索入力（ユーザ管理と同一スタイル・`id="contractSearch"`）＋「全 N 件」件数表示。**クライアント側フィルタ**（全件ロード済み `list_contracts` のため。`filterContracts` が 講師名・学校名・お客様ID・契約No(表示形式) を小文字 includes で絞り込み・`renderRows` で即時再描画・該当なしは「該当する契約はありません。」）。API・DB変更なし。テスト `test_contracts_keyword_search` 追加＝**new 474件通過**。実画面で 全2件→「コマ検証」で1件→該当なし0件を確認。**本番未反映**。
- **両システム 講師の承認管理カード（アコーディオン）の視認性改善（改修依頼 202607201858）**（2026-07-20）: 講師の承認管理（`backend`=生徒カード／`new_backend`=学校カード、いずれも `tutor/approval.html` のネイティブ `<details>/<summary>`）に、クリックで開閉できることを**文字に頼らず**示す改修。①summary右端に**下向きシェブロン**SVG（`ACCORDION_CHEVRON` 定数＝旧・新で同一）を配置、**開いている間は CSS `.accordion-card[open] .accordion-chevron { transform: rotate(180deg) }`（`transition .25s`）で上向きに反転**。②`.accordion-card:hover` で**シャドウ持ち上げ＋境界色ダーク**（`cursor-pointer` は既存）。③Safari の既定マーカーは `.accordion-summary::-webkit-details-marker{display:none}` で抑止（独自シェブロン使用）。旧・新で**同一クラス設計・同一CSS**（体験統一）。**スコープ判断**: 事務/営業/経理/運営ダッシュボードの「パイプラインカード」（`office|sales|finance/queue.html`・`admin/dashboard.html`）は「カード選択→詳細ドロワー表示」のマスター詳細型で**その場開閉のアコーディオンではない**（既に `cursor-pointer`＋`hover:brightness-95`＋選択リングあり）ため、下向きシェブロン（＝下に展開の意）は意味が食い違うので**対象外**とした。サーバ・API・JS挙動・他画面への影響なし（CSS＋summaryマークアップのみ）。テスト: `new_backend/tests/test_phase2_pages_and_bulk.py` に1件（承認管理ページにホバー/回転CSS・シェブロンSVG・`${ACCORDION_CHEVRON}` を含む）＋`backend/tests/test_accordion_ui.py` 新設1件＝**legacy 289件・new 470件通過**。Playwright で新の承認管理に同一マークアップのサンプルカードを注入し、開=上向き/閉=下向き・ホバーのシャドウを実画面で目視確認（表示のみ・データ変更/メール送信ゼロ）。**本番未反映**。
- **EMPS 新規ユーザー登録フォームの1行化＋契約管理の右端整列（改修依頼 202607201825）**（2026-07-20）: ①**ユーザー管理（`admin/users.html`）の新規ユーザー登録**を、下段別行にあった「招待メールを送る」ボタンを ロール・氏名・メール と同じ1行へ移動（`grid items-end md:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)_auto]`）。ラベル上置きのため `items-end` で入力欄とボタンの下端を揃え、ボタンは入力欄と同じ高さ `h-[42px]`。氏名・メールは等幅 `minmax(0,1fr)`（ボタン分を均等に縮小＝右端はみ出しなし）。「Noは自動で割り当てられます。」注記はフォーム左下に維持（`id="roleHint"` 不変＝ロール別文言のJS `syncRoleFields` に影響なし）。スマホ(md未満)は縦積み＋ボタン全幅。②**契約管理（`admin/contracts.html`）の右端ライン整列**: 操作列のボタンが左揃え＋「無効化」ボタンの条件表示で行ごとに「削除」ボタンのX位置がずれていた（＝バラバラ）のを、操作列を `md:justify-end`（＋見出し `md:text-right`）で右寄せに統一（全行同じX）。さらに上部ツールバー2行（`＋新規登録`エリア・`CSVで一括登録`）に `px-4` を付けてテーブルのセル(`px-4`)と左右パディングを揃え、**「＋新規登録」の右端とテーブル最右列「削除」の右端が一直線**に（実測: 1366幅で 1334px vs 1333px＝差1px＝枠線分・1920幅でも1888/1887・全行一致）。モバイルはカード化(`<768px`)で `md:justify-end`/`md:text-right` が効かず従来の左揃えを維持。サーバ・API・JS・他画面・他サービスへの影響なし（表示のみ）。テスト: `test_phase2_pages_and_bulk.py` に2件追加（`test_invite_form_inline_single_row`＝4カラム1行グリッド・h-[42px]・注記位置／`test_contracts_action_column_right_aligned`＝md:justify-end・th右寄せ・ツールバーpx-4）＝new 469件通過。Playwright で users/contracts の実画面を目視＋右端X座標を実測確認（表示のみ・メール送信ゼロ）。**本番未反映**。
- **EMPS 講師の業務連絡表ヘッダーのレイアウト再設計（改修依頼 202607201442）**（2026-07-20）: 講師の報告書一覧（`tutor/reports.html`）の実績入力より上のヘッダーを、紙面模倣のセル内側ラベル（強制`<br>`改行・幅ガタつきあり）から**ラベル上置き＋6カラムグリッド**へ全面再設計。xl: `[事業所の名称・組織単位(4)|教室名(2)] / [事業所の所在地(4)|就業場所(2)] / [氏名(2)|講師番号(2)|お客様ID(2)] / [従事業務内容(6)]`＝4列目の縦整列ラインが全行を貫通し、長い情報は広く・番号類は狭く。**対象月パネル**（対象月セレクト＋当月授業なし申請）は独立カード化（xl以上=右カラム17rem・モバイル/タブレット=先頭に全幅＝コンテキスト先行）。**追加修正（同番号）**: ①カードは `xl:self-start` で内容の高さにフィット（縦中央寄せ＋ストレッチによる間延び・浮きを解消）②カード内はフォーム群と同じラベル上置き・左揃え（sm〜lg=1行ツールバー・トグルはセレクト行と下端揃え `sm:items-end`・xl=縦積み）③当初の `max-w-6xl` 上限を撤去＝**ヘッダー全体が親コンテナの右端まで100%広がる**（表示倍率を下げてもカード右側に空白ができない）。**さらに追加修正（同番号）**: ①**対象月をカードから他の項目と同じプレーン形式へ**変更＝「教室名」「事業所の名称・組織単位」とラベル・入力欄の上端Yが完全に揃う（`aside`のカード枠 `rounded-lg border bg-slate-50 p-4` を撤去）②表記を「〇〇年〇月分 月分業務連絡表」→「〇〇年〇月分 業務連絡表」（セレクトの「月分」とラベルの「月分」が重複していた冗長表現を解消）③当月授業なしトグルの文言を「この月は授業なし（長期休業等）」→「この月は授業なし」（HTML初期値＋JS再描画 `renderNoLessonToggle` の両方を同時更新。申請中表示「〇月分は授業なし（申請中）」は不変）④ツールバーのボタン文言を「前回の記入分をコピー」→「前回コピー」、「先月の記入分をコピー」→「先月コピー」に短縮（id・onclick・title属性・コピー処理ロジックは不変）。入力欄スタイルはページ下部（弊社担当など）と同一トークン（rounded-lg px-3 py-2）へ統一し、氏名は読取専用入力と同じ見た目のボックスに。**入力欄の id・data-meta・data-fieldgroup は全て不変**＝保存・契約ロック（applyContractLocks）・表示フラグ（data-fieldgroup切替）・e2e（#monthFilter/#dispatchPlaceSchool）に影響なし。**付随修正**: 上部のタブ一覧パネルが中間幅（タブレット等）でボタン3個に押されて幅0になり、空状態メッセージが1文字ずつ縦折返しで約450pxに膨張する既存の押し潰れを解消（ツールバー `sm:flex-wrap`＋`#reportList sm:min-w-[16rem]`＝幅不足時はコピーボタンが次行へ折返す）。サーバ・他画面変更なし。テスト: `test_phase2_pages_and_bulk.py` に `test_tutor_reports_header_layout` 追加（2ゾーン構成・label for形式・強制改行の撤去・id/data属性の保持）＝new 467件通過。Playwright で 1366/1920/820/390px の実画面スクリーンショット確認済み（表示のみ・メール送信ゼロ・JSエラーなし）。**本番未反映**。
- **EMPS 業務連絡表（参照）に差戻し理由欄を追加（改修依頼 202607201303）**（2026-07-20）: 「報告書を確認」→「yyyy年mm月分 業務連絡表（参照）」（`report_view.html`）の**「従事業務内容」の直下**に**差戻し理由欄**を新設。差戻しが行われた報告書のみ、見出し**「差戻し日時/理由」**の下に**差戻しの都度を1行ずつ古い順に列挙**する＝`yyyy年mm月dd日（w）hh:mm　/　（差戻理由）`（日時はJST・曜日つき・ゼロ埋め、区切りは全角スペース　/　）。当初は画面先頭のパネル（差戻し理由＋差戻し元＋日時・現行/履歴で赤/橙の出し分け）だったが、同番号の修正依頼で**従事業務内容の下・複数行リスト形式**へ変更（先頭パネル・差戻し元表示・赤/橙の出し分けは撤去）＝さらに続く修正依頼で見出しを「差戻し理由」→「差戻し日時/理由」に、各行の接頭辞「差戻し日時/理由：」を削除。**バックエンド・DB変更なし**＝各差戻しの日時・理由はレスポンスの `events`（action=return/approve_return_request の `created_at`／`comment`）からフロントで導出（対象アクションはモデルの `_last_return_event` と同一）。実装は `new_backend/app/templates/report_view.html` のJSのみ（`formatReturnDateTime`＝Intl.formatToPartsでJST曜日つき整形／`returnEvents`／`returnReasonSectionHtml`）＝他画面・PDF・CSV・他サービスへの影響なし。テスト: `test_tutor_fixes.py::TestLastReturnComment` に2件（テンプレ配線＝従事業務内容の直下・行フォーマット・旧関数撤去の静的検証＋事務ロール視点で差戻しイベントの created_at/comment が取得できることの検証。`MAIL_BACKEND=console` で実送信ゼロ）＝new 466件通過。**本番未反映**。
- **メール**: 送信キュー(outbox)化＝1通ずつ間隔送信＋`MAIL_BACKEND`(smtp/console)／`mailmode.sh`(sandbox/off/live) 導入。
- **本番クリーン投入** `seed_production.py`＋migration 0014 の本番ガード。検証用サンプルユーザーは単一ロール（§2）。
- **担当管理**「＋担当を追加」を 講師→生徒 の検索式UIへ刷新（生徒候補API・No検索）。
- **docs 再編**: 2システム別フォルダ（`イスト勤怠レポート for 代々木進学会/`・`for EMPS/`）＋共通(`DATA_MODEL`/`INFRASTRUCTURE`/`HANDOFF`)＋`OLD/` に分類（本ファイルも含む）。
- **EMPS 講師フォームの時間自動計算**（2026-07-07）: 担当時限のコマ数×50分→右隣の担当業務（分）、（コマ数−1）×10分→休憩時間（分）を自動入力。業務開始〜終了時間は自動計算（開始8:40固定・手動入力不可）で、各分数の1分単位の手動修正に連動。詳細は `docs/イスト勤怠レポート for EMPS/SPECIFICATION.md` §9「入力の自動計算」。事務修正画面は従来どおり手動入力。**本番は手動デプロイのため未反映**（§6参照）。
- **EMPS 1〜9分手入力の事前確認フロー**（2026-07-07）: 担当業務・副担当業務（分）に10分単位でない値（1の位1〜9）がある報告は、提出時に既存の事務事前確認フロー（講師→事務→学校→事務→営業、`awaiting_office_precheck`）へ自動切替（月分超過と同じ仕組みに条件追加）。提出時ポップアップ・講師承認管理の4ステップ表示（運営へ依頼→学校へ依頼→学校承認→運営承認）・「提出」ボタン表記・発動理由のイベント自動記録つき。詳細は同SPECIFICATION §3.3。**本番未反映**。
- **EMPS 通知メールの宛名ラベルを「生徒」→「学校」へ統一**（2026-07-08）: 事務宛て提出通知（【業務連絡表】報告書が提出されました）ほかメールテンプレート8件を修正（対象生徒→対象学校・生徒名→学校名・担当生徒→担当学校）。EMPS では `student_name` に学校名が入るためラベルのみ変更（値は不変）。未処理通知の本文フォールバック「生徒未設定」→「学校未設定」も修正。「生徒」は指導報告・指導時間確認票（legacy）側の呼称のため legacy のメールは変更なし。テンプレートに「生徒」が混入しないガードテスト付き（test_mail_queue）。**本番未反映**。
- **EMPS 講師フォームのスマホ入力UI**（2026-07-08）: 画面幅768px未満では明細テーブル（横スクロール）を表示せず、記入済み行の一覧（日付・開始・終了・交通費・事由。事由＝有給/欠勤/自己都合/学校行事、勤務は空欄）＋日付タップで開く明細詳細シート（1日分をまとめて入力。担当時限は `①08:40〜09:30` 形式の時間帯つきトグル）で入力する方式へ変更。シートの保存はフォームの該当行へ反映して閉じ、サーバ保存は従来どおり画面下の「保存/更新」（スマホでは画面下部へ吸着表示）。行入力を唯一のデータ源とするため保存・集計・提出判定はPCと共通で、PC表示・事務修正画面・report_view/PDF/CSV は変更なし。詳細は同SPECIFICATION §9「スマホ入力UI」。**本番未反映**。
- **EMPS 講師「先月の記入分をコピー」**（2026-07-08）: 報告書一覧の「前回の記入分をコピー」の右隣にボタンを追加。選択中の学校の先月の報告書の明細（種別含む）を、日付を「同じ第N曜日」（例: 6月第1水曜06/03→7月第1水曜07/01）で当月へ変換して当月フォームへ反映する。当月に存在しない第5週などの行は日付空欄＋件数通知。業務開始〜終了時間は分数から自動再計算し、反映のみでサーバ保存は従来の「保存/更新」ボタン。実装は `templates/tutor/reports.html` のJSのみ（サーバ・他画面への影響なし）。詳細は同SPECIFICATION §9「記入コピー」。**本番未反映**。
- **EMPS 種別に自己都合・学校行事を追加＋日付ボックス内の曜日併記修正**（2026-07-08）: 種別プルダウン（講師フォーム・事務修正）へ `personal_reason`（自己都合）/ `school_event`（学校行事）を追加。選択すると担当時限＝選択不可・担当業務（分）＝0固定（副業務・採点・休憩・交通費・内容は手動入力可）。集計は勤務日数に含めず「自己都合/学校行事：N回」表示（講師フォーム・参照ビュー・PDF・事務/営業/経理グループサマリ）とし、入力した副業務等の分は各列合計に含める。副担当業務への1〜9分手入力は従来どおり事前確認フローの判定対象。また、明細の日付セルで曜日 `(火)` がテーブル拡張時にボックス外へ出る崩れを修正（ラッパー幅124px固定＝`2026/07/07 (火)` 形式で枠内表示）。詳細は同SPECIFICATION §8（種別）・§9（自動計算）。**本番未反映**。
- **代々木進学会 指導日報PDF**（2026-07-09）: 紙の「指導日報」様式（`docs/イスト勤怠レポート for 代々木進学会/原本_日報.pdf`・A4縦・1ページ5日分・日ごとに会員認め印欄）を忠実再現するPDFダウンロードを追加。`GET /api/reports/export-daily`（対象選定・権限は既存 `/export`＝指導時間確認票と共通ヘルパー `_reports_for_export` を共用、ファイル名は仕様どおり `指導日報_yyyy年mm月.pdf` 固定）。保護者承認を通過し承認が有効な指導日の枠に会員認め印（朱色二重丸の電子印＝承認日JST・「会員」・保護者名。差戻し中/クローズは押印しない）を描画。ヘッダー学年は月内最後に記入された学年。画面は講師承認管理・保護者承認管理/報告書・運営ダッシュボードの既存PDFボタン横に「指導日報PDF」(teal)を併設し、既存ボタンは「指導時間確認票PDF」へ改称。実装は `backend/app/services/daily_report_pdf.py`（原本PDFからpdfminerで実測した座標で描画）＋`services/pdf_fonts.py`（フォント登録の共通化）。**DBマイグレーション不要**（0017/0018の既存カラムのみ使用）・EMPS側への影響なし。テスト: `tests/test_daily_report_export.py` 追加、backend 248件通過。**本番未反映**。
- **EMPS 講師の報告書一覧に「承認依頼」ボタン＋講師番号読取専用＋承認管理ボタン改称**（2026-07-10）: 入力フォーム下部（保存/更新・取消の右端）に「承認依頼」ボタンを追加（保存→提出の合成・当月の下書き/差戻しのみ表示）。講師番号はアカウント情報由来の読取専用へ。承認管理の「学校へまとめて承認依頼」は「承認依頼」へ改称。**本番未反映**。
- **EMPS 承認依頼の確認ポップアップ**（2026-07-10）: 報告書一覧・承認管理の承認依頼（管理へ提出・再依頼含む）に確認ポップアップを追加。1〜9分手入力（事前確認フロー）は既存のワークフロー変更ポップアップが確認を兼ね、必ず1つだけ表示。**本番未反映**。
- **EMPS 事務修正モーダルを講師フォームと同一入力仕様へ（計算コア共通化）**（2026-07-10）: 明細の入力・自動計算コアを `static/js/work_report_calc.js`（WorkReportCalc）へ抽出し、講師フォーム（PC行＋スマホシート）と事務ダッシュボードの修正モーダルで共有。モーダルは種別活殺・担当時限ポップオーバー（コマ設定対応）・自動計算・労基休憩・同日重複ガード・集計欄・26行空行を完備。数値の空欄は0埋めせず空のまま保存へ修正。実体のない chat.js 読み込み（404）も削除。詳細は同SPECIFICATION「入力の自動計算」。**本番未反映**。
- **EMPS 事務修正通知の改修**（2026-07-10）: メール本文を「下記の業務連絡表が**イスト事務担当者**により修正されました。」へ変更（担当者の個人名を出さない・コメント差出人も統一）。宛先は常に講師＋学校（学校承認スキップ校の除外を撤廃）。根本バグ修正＝通知の学校解決が assignment.parent_id のみ参照で**契約のみ紐付きの学校に通知が届かなかった**→権限系と同じ「紐付け→契約」フォールバックへ統一（承認依頼・完了通知など全学校宛メールに効く）。**本番未反映**。
- **代々木進学会 指導月報**（2026-07-10）: 紙の「指導月報」様式（`docs/イスト勤怠レポート for 代々木進学会/原本_月報.pdf`）を電子化。講師メニューに「月報作成」（`/tutor/monthly-report`）を新設し、担当×対象月で1件の月報（学年※必須／問題点と対策1〜5※1件以上必須／志望校／テスト結果／指導実施日・予定日カレンダー＋指導時間合計（報告書から自動反映可）／今月を振り返って／連絡事項）を承認依頼前に作成・更新できる。**承認依頼は月報の必須項目が揃うまで 422 でブロック**（スキップ家庭も同じ）。保護者は報告書（参照）画面で月報を確認し、**承認と同時に保護者記入欄（ご要望/連絡事項）の入力が必須**（`parent_note`。講師は記入不可・月報が無い旧月は従来どおり承認可）。最終承認後は既存2種PDFと同じ画面に「指導月報PDF」（`GET /api/reports/export-monthly`・原本右面＝報告用（小学生）を実測座標で忠実再現・会員認め印は日報と同一判定・ファイル名 `指導月報_yyyy年mm月.pdf` 固定）を併設（月報が作成済みの担当×月のみ表示）。DB は `monthly_reports` テーブル追加（**migration 0019**・デプロイ時に自動適用）。既存テストは conftest の月報シードで前提を満たし、`tests/test_monthly_reports.py`（11件）追加＝legacy 260件通過。詳細は同SPECIFICATION §13（操作手順は OPERATION_MANUAL の講師・保護者・運営各節に追記済み）。**本番未反映**。
- **EMPS 参照画面・PDFを講師フォームの全項目フォーマットへ**（2026-07-09）: 「報告書を確認」（`report_view.html`）とPDFダウンロード（`services/export_service.py`）に、講師フォームの全項目（事業所の名称・組織単位／教室名／所在地／氏名／講師番号／お客様ID／従事業務内容＋弊社担当／委託業務（契約より）／スケジュール欄／要望連絡事項／定期代セクション）を漏れなく表示・出力するよう拡張。基本情報は2列（左＝事業所・右＝講師）、明細＋サマリの下に連絡事項・定期代。委託業務は列定義スナップショットから導出、定期代は全項目未記入なら「記入なし」、未入力メタは「-」。参照画面・単一PDF・一括PDFとも同一で、CSV・講師フォーム・事務修正画面は変更なし。詳細は同SPECIFICATION §「デフォルトフォーム」補足。**本番未反映**。
- **代々木進学会 講師の報告書一覧テーブルの折り返し解消（省スペース表示）**（2026-07-13）: 100%表示（1366px級）でも9列（回数・指導日・在室した時間帯・休憩・在室時間・指導時間数・教科・状態・アクション）が折り返さないよう再設計。時間量は**HH:MM表示**（例: 4時間30分→04:30・`lesson_time.js`の表示ヘルパー`hhmmLabel`＝計算規約ではない）・日付は`7/12(土)`短縮・時間帯は`17:03～18:32`（スペースなし）・数値列は右揃え等幅（tabular-nums）・セル余白px-2・全セルnowrap・教科は幅超過時に省略記号＋title・編集/削除はテーブルのみ小型（スマホカードは従来サイズ）で縦積み防止のnowrapグループ化。フォーム列はlg〜xl=340px／2xl以上=380px（**xl=1280pxは1366px画面を含むため**）。副修正＝宿題A/B/Cラジオはサイドパネル内（lg以上）で縦積み（3列だと折り返すため）・自動計算ラベルを「（自動）」へ短縮。フォームの入力・保存・API・他画面への影響なし（合計指導時間の文言・スマホカード・参照画面は従来表示のまま）。**本番未反映**。
- **代々木進学会 指導時間の1分単位入力＋在室時間表示＋指導時間数の15分切り捨て**（2026-07-13）: 講師の報告書作成で開始・終了時刻をネイティブtime入力（1分単位・PC直接入力/スマホはドラムロール）、休憩等の時間を数値入力（1分単位・0〜180分）へ変更し、5分刻みセレクトと「指導時間数は0.5時間（30分）単位」の保存ブロックを撤廃。フォームに**在室時間（＝終了−開始−休憩・1分単位）**と**指導時間数（＝在室時間の15分単位切り捨て。例：在室1時間29分→指導1時間15分）**の自動計算表示を追加（休憩が在室以上＝在室0分以下のみ保存不可）。計算は共通コア＝`backend/app/services/lesson_time.py`／`backend/app/static/js/lesson_time.js`（**同一ルールの複製・同時更新必須**）へ集約し、講師一覧・参照画面（report_view）・保護者画面・承認管理・サイドバー合計・通知メールの合計時間・月報の指導時間合計自動反映・**指導時間確認票PDF**（明細/合計/月計。時間数は四捨五入0.5h→**0.25時間刻みの正確表示**（例:1.25）へ）まで全て切り捨て後の指導時間数で統一。一覧・参照の表は「在室した時間帯／休憩／在室時間／指導時間数」列構成へ。DB保存は従来どおり開始・終了・休憩のみ＝**migration不要**・スキーマ変更なし（既存データは旧30分ルール下で15の倍数のため表示値は不変）。テスト `tests/test_lesson_time.py` 追加。詳細は同SPECIFICATION §7補足「指導時間の計算規約」。**本番未反映**。
- **EMPS ユーザ管理のレイアウト最適化＋コピーの軽量ダイアログ化（改修依頼 202607171705）**（2026-07-17）: 202607171557（コピーボタン追加）で画面サイズ100%時にユーザ管理一覧の折返し・ボタン肥大が発生したためレイアウトを全面見直し。①**ユーザ管理一覧**＝ロール列をチェックボックス5個（`min-w-72`）から**バッジ表示**へ変更し、行内「更新」ボタンを廃止（**ロール変更＝営業・事務の兼務設定は「詳細」ドロワーの「ロール設定」に集約**。API `PATCH /api/w/users/{id}/roles` は変更なし）。状態・招待状態もバッジ化、操作ボタン（詳細・コピー・再送・取消）はPCコンパクト表示（44pxタップ領域はスマホ `.row-actions` のみ）、セル余白 `px-3 py-2`＋`whitespace-nowrap` で折返し解消。CSVツールバー2段・ロールタブ・検索もコンパクト化。②**契約のコピーを専用API化**＝フルフォームのプレフィル方式（しつこい）を廃止し、**講師・学校の2項目だけ選ぶ軽量ダイアログ**＋**新規 `POST /api/w/contracts/{id}/copy`**（契約内容はサーバ側で複製＝`_DETAIL_FIELDS`＋workload_cases/period_slots deepcopy＋委託業務カラム。自動発番・同一講師×学校409・講師/学校でない指定422・メール送信なし）。フロントの `copyMode`/`copySource` プレフィル機構は撤去。③**ユーザーコピーのダイアログ整理**＝コピー元を見出し下サブテキストへ・氏名/メール2カラム・注記1行（APIは変更なし）。**DBマイグレーション不要**。テスト: `TestContractCopy` を `/copy` エンドポイント向けに書き換え（4件＝全項目複製・409・422・404・送信キュー空）＝new 464件・legacy 288件通過。実機でコピーAPI 201/409・複製一致・物理削除クリーンアップまで確認。詳細は同SPECIFICATION §「コピーして新規登録」「レイアウト最適化」・OPERATION_MANUAL。**本番未反映**。
- **EMPS ユーザー・契約の「コピーして新規登録」（改修依頼 202607171557）**（2026-07-17）: ユーザ管理・契約管理の一覧に**「コピー」ボタン**を追加し、既存のユーザー／契約を土台に新規登録できるようにした（各行の操作列。事務・営業・経理・管理責任者が利用可）。①**ユーザーのコピー**＝一覧の「コピー」→モーダルで**氏名・メールのみ新規入力**（どちらも重複はエラー＝氏名は未削除ユーザー内・メールは一意制約で既存/削除済み含め弾く）。ロール（複数ロール含む）・利用システム（new/legacy）・学校の承認スキップ設定を**コピー元から複製**し、**招待メールを送らず直接作成**（初期パスワード `Passw0rd!`・初回ログイン時に変更必須。No はコピー元の主ロール帯で自動採番。電話番号など個人情報は複製しない）。API=**新規 `POST /api/w/users/copy`**（`services/user_service.copy_user`。`create_initial_user` と同じ採番・所属・tutor_no 規約）。②**契約のコピー**＝一覧の「コピー」→契約編集ドロワーを**作成モードでコピー元の全項目プレフィル**（就業場所・期別委託業務・コマ設定・副業務・任意項目列・表示フラグ・契約期間 等）で開き、**講師・学校のみ選び直して**登録（契約番号は新しく自動発番・同一講師×学校は既存の409）。契約コピーは**バックエンド変更なし**（既存の `POST /api/w/contracts` をそのまま利用＝クライアント側でプレフィル。`renderForm` の編集判定を `editingId` 基準に変更＋`copyMode`/`copySource` を追加。**→202607171705で専用API `POST /api/w/contracts/{id}/copy`＋軽量ダイアログへ変更・プレフィル機構は撤去**）。**DBマイグレーション不要・実メール送信なし**。テスト: `test_user_copy.py` 新設（11件＝複製内容・氏名/メール重複・404・権限・管理責任者の職務分掌・送信キューが空＝メール送信ゼロ）＋`test_contracts.py` に `TestContractCopy`（2件＝コピー相当ペイロードの全項目引継ぎ・自動発番・同一講師×学校409）。詳細は同SPECIFICATION §8（ユーザー管理／契約管理）・OPERATION_MANUAL。**本番未反映**。
- **EMPS 契約に「就業場所」を追加（契約編集→講師の報告書一覧・参照画面・PDFへ反映）**（2026-07-16）: 契約管理（事務・営業・経理・管理責任者共通）の契約編集ドロワーとCSV入出力に「就業場所」（`work_assignment_profiles.work_location`・VARCHAR(255)）を追加（**migration 0015＝new側 work_alembic_version**・デプロイ時に自動適用）。講師の報告書一覧（業務連絡表ヘッダー）では「事業所の所在地」の下に就業場所行を表示（契約由来・講師読取専用。サーバー側 `_CONTRACT_LOCKED_META_KEYS` でも上書き防止）。参照画面（report_view）は基本情報グリッドの左列4行目、PDFエクスポートも「事業所の所在地」の直下（左列のみ・右列空欄）に出力。保存済み報告書の meta はスナップショットのため過去分は「-」表示（契約更新の反映タイミングは所在地・教室名と同一挙動）。CSVテンプレートに「就業場所」列が増えたため**旧テンプレートでの再取込は再エクスポートが必要**（列不足エラーになる）。**本番未反映**。
- **EMPS 契約の担当業務を「前期・後期」の2本必須へ改修（改修依頼 202607160921）**（2026-07-16）: 契約編集の担当業務を旧「①〜③（①必須・追加式）」から**前期・後期の2本必須（追加なし）**へ変更（データ上は `task_name_1`=前期/`task_name_2`=後期・`workload_cases[].task_index` 1/2。`task_name_3` は新規保存で未使用）。各期に**委託業務名（必須）・委託業務ID・個別契約ID＋月時間（分）・週コマ（任意）・適用期間（必須・前期後期の重複不可）・期別コマ設定（`workload_cases[].slots`。旧契約単位 `period_slots` は読込互換）**を設定。講師の報告書一覧は**入力タイミング（今日JST。過去月の報告書は月内へクランプ）がどちらの適用期間内かで担当業務列（常に1列）・コマ設定を自動適用**（`for-tutor?target_month`。※同日の修正依頼で「期切替が月途中の月は両期の2列・行の日付で使い分け」を廃止し、今日基準の1列＋全行同一コマ設定へ変更＝解決は `contract_form_service.active_term_case`／JS共通コア `activeTermCaseForMonth`・`termSlotsForMonth` の一箇所ずつ）。承認ルートは月分超過に加え**週コマ超過（暦週月〜日ごとの担当時限コマ数合計・`engine.exceeds_weekly_lessons`）でも事務の事前確認フローへ切替**。要望連絡事項は「【前期】業務名：（月　3,000　分固定　：　週15コマ）[適用期間]」形式＋契約期間、委託業務（契約より）は前期・後期の名称/ID類を `meta.task_reference` へスナップショット（サーバ側ロック・report_view/PDFも同一表示）、スケジュール欄は期別コマ設定「【前期】① 8:30〜9:20、…」を自動反映。事務修正モーダルも行の日付で期を解決（計算コアの `termSlotsForContext` ほかを共有）。CSVは担当業務列を前期/後期形式（名/ID/個別契約ID＋月時間/週コマ/適用開始/適用終了）へ変更＝**旧テンプレートは再エクスポートが必要**・期別コマ設定はCSV対象外（取込時も保持）。検証は `term_payload_errors` に集約（画面・API・CSV共通）。**既存データの移行なし（本番運用前のため新規分から適用・migration不要）**。旧形式契約は編集時に新仕様の入力が必要（他フィールドのみの部分更新は可）。テスト: 契約/超過フロー/分手入力/PDF/ロックの各テスト更新＋週コマ超過ほか新規（new 391件通過）。**本番未反映**。
- **EMPS コマ設定の自動並べ替え＋事前確認メッセージの理由別表示（202607161412）**（2026-07-16）: ①コマ設定は保存時に**開始時刻順へ自動で並べ替え**（①＝最も早い時間帯。検証後に `_validate_slot_list` が整列＝画面・API・読込時のモデル化で共通。旧 `period_slots` も読込時に整列）。②講師の承認管理の事前確認待ちメッセージを発動理由別へ＝**週コマ超過は「週分が契約のコマ分を超えているため、事務担当の事前確認を待っています」**（従来は月分超過の文言に固定）。理由は提出イベントの「【事前確認】…」記録から導出し複数該当は併記（`approval.html precheckReasonLabels`・記録が無い旧データはクライアント判定へフォールバック）。**本番未反映**。
- **EMPS コマ設定の時間順制約を撤廃（202607160921追加修正）＋日付未入力行の提出ガード（202607161328）**（2026-07-16）: ①契約のコマ設定（前期・後期とも）はコマ番号が時間順でなくても保存できるように（例: ⑤に①より早い朝の時間帯を追加可）。検証は「どの2コマも時間帯が重ならない」ペア判定へ変更（`schemas/contracts._validate_slot_list`＋契約編集画面の `collectPeriodSlots`）。自動計算（業務開始＝最早選択コマの開始・休憩＝コマ間の隙間）は選択コマを開始時刻順に並べ替えて算出するよう共通コア `slotSelectionMetrics` を修正（時限未選択の行の開始も時間割の最早開始へ）。②記入があるのに日付が未入力の明細行を含む報告書は**提出不可**（`/action`・`/bulk-action` の submit で422／スキップ。空欄行は対象外・下書き保存は許容）。事務修正（`/office-edit`）でも同ルールで保存不可（＋同日重複ガードも追加）。判定はサーバ `_undated_line_number` とフロント共通コア `findUndatedLineIndex` で同一。講師フォームは提出前チェック＋提出エラーの画面表示（従来は未表示だった）、承認管理・報告書詳細もエラーをトーストで表示。**本番未反映**。
- **代々木進学会 提出締切通知（画面バナー＋メール・改修依頼 202607161428）**（2026-07-16）: 講師向けに指導報告の提出締切（＝**対象月の翌月第一営業日**。営業日=土日・日本の祝日（`jpholiday`）・`BUSINESS_CLOSED_DAYS`（既定 12/29〜1/3 の年末年始休業）を除く日）を2段階で通知。**①画面バナー**＝講師の全画面（`base.html` ヘッダー黒帯の下段・スライドイン・締切日チップ強調・「入力する」CTA）に月中通知日（`DEADLINE_NOTICE_MIDMONTH_DAY` 既定15日）〜締切当日まで表示し、**締切前日からは赤の至急表示**へ自動切替（講師ロールのみ・今日の日付だけで判定＝DB不要・常時有効）。**②メール**＝日次09:00 JSTジョブ（`services/deadline_service.run_deadline_notice_job`）が、1回目【重要】（月中通知日〜締切2日前の窓）・2回目【至急確認依頼】（締切前日〜締切当日の窓）を**月×種別につき1回だけ**、未提出講師（＝有効な legacy 担当を持ち、対象月の報告書が「未作成」または「draft/差戻し残あり」。提出済み講師・無効担当・EMPS 側担当は対象外）へ送信キュー（mail_outbox）経由で配信（窓方式のためジョブ停止日を挟んでも追い送りされる）。送信済みガードは新テーブル `deadline_notice_sends`（**migration 0020**・アプリ内通知ログは notifications に deadline_first/deadline_eve で記録）。**メール送信は `DEADLINE_NOTICE_ENABLED=true` のときのみ（既定 false＝誤送信防止。バナーはフラグと無関係に表示）**。本文は `templates/email/deadline_first.txt`/`deadline_eve.txt`（依頼の文面どおり・締切日は「8月3日（月）」形式）。依存に `jpholiday` を追加（**要再ビルド**）。テスト `tests/test_deadline_notice.py`（営業日計算・表示/送信窓・対象者抽出・二重送信ガード・バナー表示の16件）＝legacy 288件通過。**本番未反映**。
- **EMPS 副担当業務の実施位置（コマ後/コマ間）＝休憩の既定計算を変更（改修依頼 202607161853）**（2026-07-16）: コマ設定（時間割）契約の休憩時間（分）の自動計算を**「コマ間の隙間そのまま」へ変更**（従来は隙間−副担当業務等）。副担当業務等（分）は既定で**最終コマの後に実施**する扱いになり、終了時間が副担当の分だけ後ろへ延びる（例: ①8:30〜9:20/③10:30〜11:20/④11:30〜12:20＋副担当50分 → 担当150分・休憩80分（70+10）・終了は④の直後+50分=13:10）。従来どおりコマ間で実施するケース向けに、休憩時間の右隣へ**「副担当の位置」セレクト（コマ後＝既定/コマ間）**を追加（コマ設定契約かつ副担当業務等の分列がある場合のみ表示。講師PC明細行・スマホ詳細シート・事務修正モーダルの3UI共通・切替で休憩＝隙間−副担当（0未満は0）へ自動再計算）。値は `form_data.lines[].secondary_placement`（`gap` のみ意味を持ち、他に記入が無い行は保存時に既定へ戻して未記入行判定・日付未入力ガードと互換）＝**DBマイグレーション不要・サーバ変更なし**（参照画面/PDF/CSV/修正差分通知は列定義スナップショット基準のため非表示。休憩・終了時間の値として反映される）。労基下限（実働6h超45分/8h超60分）・同日23:59超過ブロックは従来どおり（超過案内に「コマ間」への切替を追記）。ルールは共通コア `work_report_calc.js`（`slotBreakDecision`／`secondaryPlacementIsGap`／`normalizedSecondaryPlacement`）へ集約し3UIで共有。テスト: `test_phase2_pages_and_bulk.py` の共通コア組込検証を拡張（new 398件・legacy 288件通過）＋e2e `new-tutor-slot-placement.spec.js`（PC行/スマホシート/コマ設定なし契約の非表示・UI操作のみ保存なし＝メール送信ゼロ。前提seed: qa.tutor.slots@example.com＋コマ設定契約。未seed環境はskip）。詳細は同SPECIFICATION §「入力の自動計算」・OPERATION_MANUAL 講師節。**本番未反映**。
- **EMPS 担当業務の前期・後期「少なくとも1期」緩和＋契約管理番号の作成順自動発番（改修依頼 202607170952）**（2026-07-17）: ①契約の担当業務は**前期・後期のうち少なくとも1期**でOKへ緩和（前期のみ・後期のみの契約が可能。202607160921の「両期必須」を変更）。設定する期は委託業務名・適用期間が必須のまま・両期設定時のみ期間重複を検証・どちらも未設定は保存不可。検証は従来どおり `schemas/contracts.term_payload_errors` 一箇所（画面・API・CSV共用）＋契約画面 `collectTerms`（何も入力していない期はスキップ。コマ設定未使用時はグレイアウト中の保持slotsを設定判定から除外＝期を空にすればslotsごと外れる）。データは位置固定のまま（後期のみ＝`task_name_1`空/`task_name_2`あり→列は `task_minutes_2`）。②**契約管理番号**（`work_assignment_profiles.contract_no`・**migration 0017＝new側**・既存契約はcreated_at昇順でbackfill）: 作成順に1から自動発番（最大値+1＝途中の欠番は再利用しない。発番は `services/contract_number_service.issue_contract_no` に集約＝新規登録／CSV取込新規／`/api/w/admin/profiles` の全作成経路。更新では不変）。一覧先頭列・編集ドロワー（読取専用・新規は「保存時に自動発番」）・CSVエクスポート参考列 `契約管理番号(参考)` に5桁ゼロ詰め表示。**参考列はOPTIONAL_HEADERS＝列の無い旧テンプレートも取込可**（今回はCSV再エクスポート不要）。テスト: `TestSingleTermContracts`（7件）＋`TestContractNo`（6件）追加。詳細は同SPECIFICATION §8・DATA_MODEL §3・OPERATION_MANUAL 契約管理節。**本番未反映**。
- **EMPS 契約のコマ設定 使用/未使用＋未使用契約の手入力方式＋契約編集フォーム再構成（改修依頼 202607170831）**（2026-07-17）: 契約管理の編集に**「コマ設定を使用する」チェック**（`work_assignment_profiles.use_period_slots`・NOT NULL 既定true＝従来動作。**migration 0016＝new側 work_alembic_version**・デプロイ時に自動適用）を追加。**未使用**にすると①前期・後期のコマ設定ブロックはグレイアウト（入力・追加ボタン無効。**設定値は保持**＝画面保存は保存済みslots/旧period_slotsをそのまま送出・CSV upsertも `_CSV_PRESERVED_FIELDS` で保持）②報告書の列定義（`contract_form_service.build_column_definition`）に**担当時限列を生成しない**＝講師フォーム（PC行・スマホシート）と事務修正モーダルは**手入力方式**（業務開始時間=time手入力・担当業務/副担当業務/休憩時間=手入力・**業務終了時間のみ自動計算**＝開始＋時間(分)列合計。23:59超過と「分記入あり×開始未入力」は保存ブロック）③休憩非表示×コマ設定の併用不可検証の対象外（「使用」へ戻すPATCHで再検証・422）。判定は**列スナップショット基準**（共通コア `work_report_calc.js` の `manualStartEntry(columns)`・`computeManualEnd`。`termSlotsForMonth` は `use_period_slots=false` で常にnull）のため、フラグを後から切り替えても既存報告書の入力方式は不変（新規作成分から適用）。手入力による1〜9分単位値の事前確認フロー・労基休憩の扱い（非コマ契約と同じ＝自動引き上げなし）・report_view/PDF/CSVは列スナップショット準拠で変更なし。契約編集フォームは「基本情報／就業場所・事業所／契約期間・スケジュール／コマ設定使用／担当業務（前期・後期）／副業務／任意項目列／表示項目」の**セクション見出しつき構成へ再編**（全入力IDは不変・CSVヘルプ注記更新）。テスト: `test_contracts.py` に `TestUsePeriodSlots`（7件）＋CSV保持1件追加。詳細は同SPECIFICATION §8（契約の項目）・§9（入力の自動計算「コマ設定未使用の契約」）・DATA_MODEL §3・OPERATION_MANUAL 契約管理節。**本番未反映**。
- **EMPS 学校→運営通知の仕様変更＋講師の「当月授業なし」申請＋学校の締め日前確認メール（改修依頼 202607161140）**（2026-07-17）: ①**完了通知の宛先を事務+営業の全員へ拡大し、月末+N日の進捗ダイジェストメールを廃止**（純粋に「契約講師全員の学校承認完了」通知のみ。`.env` の `NEW_SCHOOL_PROGRESS_DAYS_AFTER_MONTH_END` 撤去・テンプレ `notify_school_monthly_progress.txt` 削除・メールの宛名/リンクはロール別= `/office/queue`／`/sales/queue`）。②**講師の「当月授業なし」申請**＝報告書一覧の対象月セレクト下のトグル（確認ポップアップつき・講師×月・全契約対象・`work_no_lesson_months`・API `GET/PUT /api/w/no-lesson-months`）。申請中の講師は完了判定の対象外（報告書の有無・状態を問わず。完了メールに「対象外（当月授業なし申請）」として明記）で、申請により全員承認が成立した場合はその場で完了メールを送る（既に完了済みの学校へは再送しない）。全講師が申請中の月は成立しない。報告書の作成・提出は制限しない。③**学校の締め日・提出確認メール設定**＝ユーザ管理の学校詳細ドロワーに「締め日・提出確認メール設定」セクションを追加（事務・営業・経理・管理責任者が編集可）: 早期チェックON/OFF・送信タイミング（締め日のN日前・0〜60・既定3）・締め日の年間設定（`◀年▶` 切替×12ヶ月の日付入力・翌月日付可→**202607161332で対象月内のみへ変更**・送信済みバッジ・未保存ハイライト。`work_school_settings`／`work_school_deadlines`・API `GET/PUT /api/w/users/{id}/school-settings`）。早期チェックONの学校のみ、「締め日−N日〜締め日当日」の窓で1回だけ営業全員へタイトル**【至急確認】**の「締め日は〇〇です、提出状況を確認してください」メール（本文に承認状況の内訳つき）を日次09:00ジョブで送信（`services/school_deadline_service.py`・窓方式=停止日を挟んでも締め日までは追い送り・締め日超過は遡及送信しない・**締め日変更でガード解除=再送対象**・全員承認済みの学校はスキップ）。DB=**migration 0018（new側 work_alembic_version・デプロイ時に自動適用）**。「未作成」ラベル＝報告書レコードなし（旧「当月授業なし」表記は申請と紛らわしいため改称）。テスト: `test_school_progress.py` 全面更新＋`test_school_deadline.py` 新設（new 439件・legacy 288件通過）。詳細は同SPECIFICATION §10（通知仕様）・DATA_MODEL §3・OPERATION_MANUAL 講師/運営/通知節。**本番未反映**。
- **EMPS 締め日設定の対象月内制限＋締め日設定CSV（改修依頼 202607161332＝202607161140の修正）**（2026-07-17）: ①**締め日は対象月内の日付のみ**（例: 1月分は1月のカレンダーのみ）＝画面は date 入力の min/max でカレンダーを制限＋キーボード入力もJSで拒否、API は `save_school_settings` の検証で対象月外を **422**（CSV取込も同ルール）。②**学校の締め日設定CSV**＝ユーザー管理画面に「学校の締め日設定CSV」操作列を追加（対象年セレクト＋エクスポート/インポート・CSVヘルプに書式節を追記）。`GET /api/w/users/school-deadlines/export?year=YYYY`（UTF-8 BOM・行=学校No×対象年・各月列は締め日の「日」・学校0件でもヘッダーのみ出力=テンプレ兼用）／`POST /api/w/users/school-deadlines/import`（学校Noで照合=schoolロールのみ・月列は「日」または対象月内の日付・**空欄=その月の締め日を削除**（エクスポート→編集→取込の往復でファイル内容がそのまま反映）・早期チェック/通知日数は空欄で現状維持＆同一学校の行間不一致はエラー・学校No×対象年の重複エラー・**1件でもエラーで全件中止**・取込による締め日変更も送信済みガード解除）。実装は `services/school_deadline_import_service.py`（user_import_service と同方針）。**DBマイグレーション不要**。テスト: `test_school_deadline.py` にCSV10件＋月内制限1件追加（CSV側の月内制限は取込エラーテスト内で検証。new 450件・legacy 288件通過）。**本番未反映**。
- **EMPS 講師起点の差戻し要求（request_return）**（2026-07-10）: 講師が提出後の報告書（事前確認待ち・学校確認待ち・事務確認待ち・営業確認待ち・**完了後**・事務差戻し中）について、現在ボールを持つロールへ理由必須の差戻しを要求できる機能。ボール保持ロール（学校/事務/営業）が参照画面の要求パネルで**許可**（→ `returned_to_tutor` へ差戻し・要求理由をコメントへ自動転記）または**却下**（理由必須・講師画面に表示）する。**要求は承認等でボールが移っても未解決のまま新しいボール保持ロールへ引き継がれる**（解決＝許可/却下/講師へ戻る差戻し/クローズのみ）。未解決状態は `work_report_events` から導出（`WorkReport.return_request_pending` ほか）＝**DBマイグレーション不要**。**メール通知なし**（設計判断。到達経路＝事務/営業タスク一覧の「差戻し要求」行・KPI算入・パイプラインバッジ・学校一覧バッジ・講師承認管理の要求中/却下表示）。職務分掌は要求の許可・却下にも適用。新アクション3種（request_return/approve_return_request/decline_return_request）は遷移表 `definitions.RETURN_REQUEST_BALL_HOLDERS` 起点で定義。詳細は同SPECIFICATION §3.6。テスト `tests/test_return_request.py`（26件）追加＝new 368件通過。**本番未反映**。

---

## 6. 重要な前提・落とし穴

- **本番は手動デプロイ**（Lightsail SSH で git pull＋up --build）。ローカルでの `up --build` は本番に反映されない。
- **`.env` は両システム共有**。衝突回避のため URL は `BASE_URL`/`NEW_BASE_URL`、送信元は `SMTP_FROM`/`NEW_SMTP_FROM` に分離済み。本番 `.env` は手動管理（リポジトリには無い）。
- **`users.email` は一意（1メール=1ユーザー行）**。同じメールに複数ロールを持たせるとログインのロール選択で破綻する（上記2参照）。
- **MailHog は開発用ダミー**。本番で実配信するには **実SMTP＋`MAIL_BACKEND=smtp` の両方**が必要（`console` 既定では送らない。上記1）。
- 共有テーブルへの列追加は **legacy 側 Alembic**（`backend/alembic/versions/`）で行い、`backend` と `new_backend` 双方のモデルに反映する。`backend` コンテナは `alembic/` をマウントしないため、マイグレーション反映には再ビルドが必要。
- （Claude Code 向け）この環境では PowerShell ツール経由の `git add/commit` が拒否されることがある。その場合は Bash ツール経由で `git` を実行する。

---

## 7. 次にやることチェックリスト

- [ ] **本番デプロイ**（Lightsail SSH: `cd ~/tutor-report-system && git pull && sudo docker compose up -d --build`）: 2026-07-07〜07-10 の改修（§5の「本番未反映」項目一式）が未反映。特に**事務修正通知が学校（スキップ校・契約のみ紐付き校）へ届かない事象はデプロイ後に解消**（通知は修正操作の時点で発火するため、過去の修正分は遡って送信されない）。
- [ ] 本番で実配信を有効化: `.env` に `MAIL_LIVE_*`＋`ENVIRONMENT=production` を設定し `sudo bash mailmode.sh live`（または手動で `SMTP_*`＋`MAIL_BACKEND=smtp`）→ `send_test_email` で配信確認（最優先）。
- [ ] 招待メールが Gmail に実際に届くことを確認。
- [ ] **提出締切メール通知（202607161428）の本番有効化**: 本番 `.env` に `DEADLINE_NOTICE_ENABLED=true` を追記して再起動（未設定のままなら画面バナーのみ有効でメールは送られない）。営業日の休業日調整は `BUSINESS_CLOSED_DAYS`、月中通知日は `DEADLINE_NOTICE_MIDMONTH_DAY`（§5「提出締切通知」参照）。
- [ ] 必要に応じて本番で `seed_production --yes` を実行しサンプル 6 ユーザーに統一。
- [ ] （任意）legacy 用の 保護者/受付/再鑑 サンプルが必要なら別エイリアスで追加。
- [ ] （任意）送信量が増えるなら SendGrid / SES へ移行。

---

## 8. 保留中の検討事項（問い合わせ番号つき）

継続の壁打ち／作業依頼で参照するための保留案件。**引き継ぐ場合は問い合わせ番号で照会すること。**

### 【EMPS-2026-0709-01】学校承認→運営通知（全講師の学校承認完了で通知）

> **2026-07-17 改修依頼 202607161140 で仕様変更**（§5参照）: 宛先を**事務＋営業の全員**へ拡大・**締切進捗ダイジェストは廃止**・講師の「当月授業なし」申請（`work_no_lesson_months`）で集計対象外にできるようになった。以下は現行仕様。

- **状態**: ✅ 実装完了（2026-07-09 初版 → 2026-07-17 改修 202607161140 反映済み）
- **現行仕様**:
  1. **即時通知**: 1つの学校に紐づく**有効契約の講師全員**（「当月授業なし」申請中の講師を除く）の当月報告書が学校承認を通過した時点で、**事務・営業（`office` / `sales` ロールの有効ユーザー全員）**へ完了メールを1通ずつ送る（1講師のみの学校はその1件で発火）。差戻し→再承認で全員承認が再成立した場合は再送する。講師の「当月授業なし」申請で成立した場合もその場で送る（既に完了済みの学校へは再送しない）。
  2. **除外**: 学校確認スキップ校（学校ユーザーの `skip_parent_approval`）・無効契約（`is_active=False`）・契約期間が当月に掛からない契約・退会済み講師・「当月授業なし」申請中の講師（メールに対象外として明記）。全講師が申請中の月は成立しない。
  3. 通知先は office / sales ロール全員（契約の `our_staff`＝弊社担当は自由入力でアカウント非連動のため学校別の送り分けは不可）。メールのリンクはロール別（`/office/queue`・`/sales/queue`）。
- **実装のポイント**:
  - 集計・送信の本体は `new_backend/app/services/school_progress_service.py`（唯一の判定ロジック `school_month_progress()`。締め日前確認メール＝`school_deadline_service` も同関数を共用）。
  - 即時通知のフックは `notification_service.send_transition_notifications`（approve 時に遅延 import で呼ぶ）＋「当月授業なし」申請API（`api/no_lesson_months.py` → `send_school_all_approved_after_no_lesson`）。
  - 「学校承認済み」= 現在ステータスが `awaiting_office` / `awaiting_sales` / `returned_to_office` / `approved`。「未作成」= 当月の報告書レコードが存在しない講師（未承認扱い）。
  - メールテンプレ: `templates/email/notify_school_all_approved.txt`（`notify_school_monthly_progress.txt` は廃止・削除済み）。
  - テスト: `new_backend/tests/test_school_progress.py`（MAIL_BACKEND=console のため実送信ゼロ）。
- **本番反映の注意**: デプロイ後、学校承認で全員が揃うと事務・営業へ実メールが飛ぶ（意図どおりの動作）。`.env` の `NEW_SCHOOL_PROGRESS_DAYS_AFTER_MONTH_END` は不要になった（残っていても無害・読まれない）。
