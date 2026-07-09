# 引継ぎ書 (HANDOFF)

別の担当者 / 別の Claude Code アカウントが作業を引き継ぐための文書。**まずこれを読み、次に `CLAUDE.md`・`docs/INFRASTRUCTURE.md` を読むこと。**

> メモ: Claude Code の個人メモリ（`~/.claude/...`）はアカウント/PCに紐づくため引き継がれない。引継ぎに必要な文脈はすべて本ファイル（リポジトリ）に集約している。

最終更新: 2026-07-09

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
- **EMPS 参照画面・PDFを講師フォームの全項目フォーマットへ**（2026-07-09）: 「報告書を確認」（`report_view.html`）とPDFダウンロード（`services/export_service.py`）に、講師フォームの全項目（事業所の名称・組織単位／教室名／所在地／氏名／講師番号／お客様ID／従事業務内容＋弊社担当／委託業務（契約より）／スケジュール欄／要望連絡事項／定期代セクション）を漏れなく表示・出力するよう拡張。基本情報は2列（左＝事業所・右＝講師）、明細＋サマリの下に連絡事項・定期代。委託業務は列定義スナップショットから導出、定期代は全項目未記入なら「記入なし」、未入力メタは「-」。参照画面・単一PDF・一括PDFとも同一で、CSV・講師フォーム・事務修正画面は変更なし。詳細は同SPECIFICATION §「デフォルトフォーム」補足。**本番未反映**。

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

- [ ] 本番で実配信を有効化: `.env` に `MAIL_LIVE_*`＋`ENVIRONMENT=production` を設定し `sudo bash mailmode.sh live`（または手動で `SMTP_*`＋`MAIL_BACKEND=smtp`）→ `send_test_email` で配信確認（最優先）。
- [ ] 招待メールが Gmail に実際に届くことを確認。
- [ ] 必要に応じて本番で `seed_production --yes` を実行しサンプル 6 ユーザーに統一。
- [ ] （任意）legacy 用の 保護者/受付/再鑑 サンプルが必要なら別エイリアスで追加。
- [ ] （任意）送信量が増えるなら SendGrid / SES へ移行。
