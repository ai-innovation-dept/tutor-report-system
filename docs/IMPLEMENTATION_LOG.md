# tutor-report-system 実施ログ

作成日: 2026年5月22日

この文書は、ここまでに実施した修正内容と確認結果をまとめた記録です。

## 1. 依存関係と TemplateResponse 対応

- `backend/pyproject.toml` の FastAPI / Starlette を互換バージョンに固定した。
  - `fastapi==0.115.2`
  - `starlette==0.40.0`
- `TemplateResponse` の呼び出しを Starlette 0.36 以降の形式に修正した。
  - 旧: `TemplateResponse("xxx.html", {"request": request})`
  - 新: `TemplateResponse("xxx.html", context={"request": request})`

## 2. 操作手順書の作成

- `docs/OPERATION_MANUAL.md` を作成した。
- ロール別の操作手順、デモアカウント、起動停止手順、リマインド通知などを記載した。

## 3. ロール別ナビゲーション

- `base.html` のナビゲーションをロール別表示に変更した。
- 他ロールのメニューが表示されないようにした。
- ログイン後の遷移先をロール別に変更した。
  - 講師: `/tutor/reports`
  - 保護者: `/parent/reports`
  - 運営: `/admin/dashboard`

## 4. 報告書ステータスと入力フォーム

- 報告書ステータスを画面上で日本語表示するようにした。
- `lesson_reports.break_minutes` を追加する Alembic マイグレーションを作成した。
- 指導時間数は DB に持たず、画面表示時に計算するようにした。
- 報告書作成・編集フォームに以下を反映した。
  - 指導日
  - 在室時間
  - 休憩等の時間
  - 指導時間数
  - 科目
  - 指導内容

## 5. 講師 報告書一覧

- `/tutor/reports` の並び順を指導日の古い順に変更した。
- 一番左の列を「回数」に変更した。
- 一覧下部に合計指導時間を表示した。
- 行ごとのステータス列を削除した。
- 下書きは「編集」「削除」、差戻しは「編集」と差戻し理由を表示するようにした。
- 月フィルタを追加し、過去月は編集・削除できないようにした。
- 生徒プルダウン変更時に一覧も対象生徒へ切り替わるようにした。
- 現在表示中の生徒名を表示するバッジを追加した。

## 6. 講師ナビゲーションと承認管理

- 講師ナビゲーションを以下の構成に変更した。
  - 報告書一覧
  - 承認管理
  - 講師番号
  - 講師名
  - 今月の合計時間
  - Logout
- `users.tutor_no` を追加する Alembic マイグレーションを作成した。
- `seed.py` に `T001`, `T002` を設定した。
- `/tutor/approval` を新設した。
- 月ごとのステッパー型承認フロー画面に刷新した。
- 月次サマリ API `GET /api/reports/monthly-summary` を追加・修正した。

## 7. 報告書 CRUD と Bulk API

- `POST /api/reports` の作成処理を確認・修正した。
- `PATCH /api/reports/{id}` は `draft` と `returned_to_tutor` のみ許可するようにした。
- `DELETE /api/reports/{id}` は `draft` のみ許可するようにした。
- 以下の Bulk API を追加・確認した。
  - `POST /api/reports/submit-to-parent-bulk`
  - `POST /api/reports/parent-approve-bulk`
  - `POST /api/reports/submit-to-admin-bulk`
  - `POST /api/reports/parent-return-bulk`
  - `POST /api/reports/admin-return-bulk`
  - `POST /api/reports/admin-receive-bulk`
  - `POST /api/reports/admin-review-bulk`
  - `POST /api/reports/admin-approve-bulk`

## 8. 簡易作成フォームと新規生徒追加

- 講師の簡易作成フォームから新しい生徒を追加できるようにした。
- 生徒プルダウン末尾に「＋ 新しい生徒を追加」を追加した。
- 選択時に生徒名入力欄を表示するようにした。
- 保存時に以下を連続実行するようにした。
  1. `POST /api/assignments`
  2. `POST /api/reports`
- `assignments.parent_id` を NULL 許容に変更した。
- 新規生徒で報告書を作成できるよう、`lesson_reports.parent_id` も NULL 許容に変更した。
- Alembic マイグレーション `0004_allow_null_parent_in_assignments.py` を作成した。
- 管理者が後から保護者を紐付けられるよう `PATCH /api/assignments/{id}` を追加した。

## 9. 運営アカウント

- 運営アカウントのメールアドレスを以下に統一した。
  - `receiver1@example.com`
  - `reviewer1@example.com`
  - `master1@example.com`
- `seed.py` を修正し、既存の旧メールアドレスを新メールへ更新するようにした。
- seed 再投入時に `is_active = true` と `Passw0rd!` のパスワード再設定も行うようにした。
- `receiver1@example.com / Passw0rd!` で JWT が返ることを確認した。

## 10. 保護者画面

- `/parent/reports` を月次承認フローに刷新した。
- 月選択プルダウンを追加した。
- 選択した1か月分だけ表示するようにした。
- 未来月は選択肢に含めないようにした。
- 過去月は参照のみとし、承認・差戻しボタンを非表示にした。
- 承認ボタンのラベルを「5月分を承認する」の形式に変更した。
- 行ごとの承認・差戻しボタンは削除した。
- 月単位でまとめて承認・差戻しできるようにした。

## 11. 運営ダッシュボードとキュー画面

- `/admin/dashboard` と以下のキュー画面を刷新した。
  - `/admin/queue/receive`
  - `/admin/queue/review`
  - `/admin/queue/approve`
- 画面構造を「月選択 → 講師選択 → 生徒別カード」に変更した。
- 生徒選択プルダウンは廃止した。
- カード1枚を「講師 + 生徒 + 月」のセットとして扱うようにした。
- カードステータスは、最も進んでいない報告書のステータスで決定するようにした。
- 行ごとの操作ボタンを廃止し、カード単位の一括操作だけにした。
- カード下部に「指導記録を見る」トグルを追加した。
- 展開時に読み取り専用の指導記録テーブルを表示するようにした。
- サマリカードの単位を報告書件数から「セット」に変更した。

## 12. 運営 Bulk API

- `POST /api/reports/admin-receive-bulk` を追加した。
  - `admin_receiver` または `admin_master` のみ実行可能。
  - 全件 `submitted_to_admin` の場合のみ `received` へ遷移。
- `POST /api/reports/admin-review-bulk` を追加した。
  - `admin_reviewer` または `admin_master` のみ実行可能。
  - 全件 `received` の場合のみ `re_reviewed` へ遷移。
- `POST /api/reports/admin-approve-bulk` を追加した。
  - `admin_master` のみ実行可能。
  - 全件 `re_reviewed` の場合のみ `admin_approved` へ遷移。
- `POST /api/reports/admin-return-bulk` は空コメントで `422` を返すことを確認した。

## 13. 主な動作確認

- `python -m compileall app`
- `python -m pytest tests -q`
- `docker compose restart backend`
- `docker compose exec backend python -m pytest -q`
- `docker compose exec backend python -m app.scripts.seed`
- 各ロールでログイン確認。
- 講師、保護者、運営の月次承認フロー確認。
- 運営の受付、再鑑、最終承認 Bulk API 確認。
- 運営差戻し Bulk API と差戻し理由表示確認。
- Docker サービスの起動状態確認。

## 14. 注意事項

- 検証用に 2026年5月、6月、7月の報告書を少数追加している。
- DB スキーマ変更を含む修正は Alembic マイグレーションを作成済み。
- 現在の実装は開発用 seed データを前提に動作確認している。
