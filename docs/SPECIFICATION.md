# 家庭教師 指導実績報告システム 仕様書

**バージョン**: 1.1.0  
**最終更新日**: 2026-06-02  

---

## 目次

1. [システム概要](#1-システム概要)
2. [登場人物とロール](#2-登場人物とロール)
3. [業務フロー](#3-業務フロー)
4. [画面一覧](#4-画面一覧)
5. [報告書ステータス一覧](#5-報告書ステータス一覧)
6. [API仕様](#6-api仕様)
7. [データモデル](#7-データモデル)
8. [未処理報告クローズ機能](#8-未処理報告クローズ機能)
9. [通知仕様](#9-通知仕様)
10. [エクスポート機能](#10-エクスポート機能)
11. [環境構成](#11-環境構成)
12. [運用手順](#12-運用手順)
13. [将来拡張予定](#13-将来拡張予定)

---

## 1. システム概要

### 目的

家庭教師の指導実績（指導日・時間・内容）を月次でデジタル記録し、保護者確認・運営承認の多段階ワークフローを経て最終確定するシステム。紙・メール・口頭による報告業務をシステム化し、承認状況の可視化・追跡を実現する。

### 対象ユーザー

| ユーザー | 役割の概要 |
|----------|-----------|
| 講師（tutor） | 指導実績を記録し、保護者・運営の承認を取り付ける |
| 保護者（parent） | 講師の報告書を確認し、承認または差戻しを行う |
| 受付担当（admin_receiver） | 運営へ提出された報告書を受け付ける |
| 再鑑者（admin_reviewer） | 受付済み報告書を再確認する |
| 管理者（admin_master） | 最終承認・運営操作の代行・ユーザー管理を行う |

### システム構成図（テキストベース）

```
+--------------------------------------------------------------------+
|                         ブラウザ (クライアント)                      |
|  Jinja2 テンプレート + Tailwind CSS + バニラ JavaScript              |
+---------------------------+----------------------------------------+
                            | HTTP (Cookie 認証)
                            v
+--------------------------------------------------------------------+
|                    FastAPI アプリケーション (port 8000)              |
|                                                                     |
|  +-----------------+  +------------------+  +------------------+  |
|  |  pages router   |  |   API routers    |  |  APScheduler     |  |
|  |  (HTML 画面)     |  |  /api/auth       |  |  (月末リマインダ) |  |
|  +-----------------+  |  /api/reports    |  +------------------+  |
|                        |  /api/users      |                         |
|                        |  /api/invitations|                         |
|                        +------------------+                         |
+----------------------------+---------------------------+-----------+
                             | SQLAlchemy (psycopg)      | aiosmtplib
                             v                           v
              +--------------------------+   +----------------------+
              |  PostgreSQL 16 (port 5432)|   |  MailHog (port 1025) |
              |  (Docker Volume)          |   |  (開発用 SMTP)        |
              +--------------------------+   +----------------------+
```

---

## 2. 登場人物とロール

### ロール一覧と権限表

| 操作 | tutor | parent | admin_receiver | admin_reviewer | admin_master |
|------|:-----:|:------:|:--------------:|:--------------:|:------------:|
| 報告書作成（当月のみ） | O | X | X | X | X |
| 報告書編集（下書き・差戻し） | O | X | X | X | X |
| 報告書削除（下書きのみ） | O | X | X | X | X |
| 報告書閲覧（自分の担当分） | O | O | X | X | X |
| 報告書閲覧（全件） | X | X | O | O | O |
| 保護者へ承認依頼送信 | O | X | X | X | X |
| 保護者承認・差戻し | X | O | X | X | X |
| 運営へ提出 | O | X | X | X | X |
| 受付（受付担当） | X | X | O | X | O |
| 再鑑（再鑑者） | X | X | X | O | O |
| 最終承認（管理者） | X | X | X | X | O |
| 差戻し（受付担当から） | X | X | O | X | O |
| 差戻し（再鑑者から） | X | X | X | O | O |
| 差戻し（管理者から） | X | X | X | X | O |
| エクスポート（自分の担当分） | O | O | X | X | X |
| エクスポート（全件） | X | X | O | O | O |
| ユーザー作成・管理 | X | X | X | X | O |
| ユーザー招待送信 | X | X | X | X | O |
| 担当紐付け管理 | X | X | X | X | O |
| チャットメッセージ送受信 | O | O | O | O | O |

### 補足

- `admin_master` は `admin_receiver`・`admin_reviewer` の操作をすべて兼務できる
- `tutor` は自分が担当する assignment に紐づく報告書のみ操作可能
- `parent` は自分の子どもの assignment に紐づく報告書のみ閲覧・承認可能
- 報告書の作成は **当月分のみ** に限定される
- 同一 assignment 同一月に既に `admin_approved` の報告書がある場合は追加不可
- 同一 assignment 同一月に `awaiting_parent_approval` 以降（進行中）の報告書がある場合は追加不可
- `draft`・`returned_to_tutor`・`closed` のみの場合は追加作成可能（`closed` は終端だが同月への新規作成はブロックしない）

---

## 3. 業務フロー

### 3.1 指導実績登録から最終承認までの全フロー

UI 上の承認管理は、講師・保護者・運営の状態を月次単位でまとめて扱う。利用者向けには次の4ステップで表示・運用する。

| ステップ | 担当 | 主な状態 | 実装上のステータス |
|---------|------|----------|------------------|
| 1. 記録 | 講師 | 当月の指導実績を登録・修正する | `draft`, `returned_to_tutor` |
| 2. 保護者依頼 | 講師 | 月内の対象報告書を保護者へまとめて送る | `awaiting_parent_approval` |
| 3. 保護者承認 | 保護者 | 月次で承認または差戻しする。承認時は運営へ自動提出される | `submitted_to_admin` |
| 4. 運営承認 | 運営 | 受付、再鑑、最終承認を実施する | `received`, `re_reviewed`, `admin_approved` |

```
【講師】                 【保護者】                【運営】
   |                        |                        |
   | 1. 指導日ごとに報告書作成                         |
   |    （下書き状態で保存）                           |
   |                        |                        |
   | 2. 保護者へ承認依頼送信 --> メール通知             |
   |    （一括または個別）      |                      |
   |                        | 3. 報告書を確認         |
   |                        |    承認 or 差戻し       |
   |                        |                        |
   | <-- 差戻しの場合 ---------------------------------|
   |  報告書を修正して再送                             |
   |                        |                        |
   | <-- 承認の場合 -----------------------------------|
   |    parent_approve 後、システムが運営へ自動提出      |
   |                        |          |           |
   |                        |          v            |
   |                        |  4. 受付担当が受付     |
   |                        |     (received)        |
   |                        |          |            |
   |                        |          v            |
   |                        |  5. 再鑑者が再鑑       |
   |                        |     (re_reviewed)     |
   |                        |          |            |
   |                        |          v            |
   |                        |  6. 管理者が最終承認    |
   | <-- メール通知 -----------------------------------|
   | <-- メール通知（保護者へも）                       |
   |                        |                        |
  完了（admin_approved）
```

### 3.2 差戻しフロー

差戻しは4種類あり、差戻し先は差戻しロールによって異なる。差戻し時はコメント入力が必須で、チャットにも自動投稿される。

```
【差戻し種別・発生元・差戻し先】

保護者差戻し（parent_return）
  awaiting_parent_approval --> returned_to_tutor
  → 講師が修正して再提出

受付担当差戻し（return_from_receiver）
  submitted_to_admin / received / returned_to_receiver --> returned_to_tutor
  → 講師が修正して再提出

再鑑者差戻し（return_from_reviewer）
  received / re_reviewed --> returned_to_receiver
  → 受付担当が再受付

管理者差戻し（return_from_master）
  re_reviewed / admin_approved --> returned_to_receiver
  → 受付担当が再受付

【差戻し後の対応】
returned_to_tutor   --> 講師が修正 --> submit_to_parent --> awaiting_parent_approval
returned_to_receiver --> 受付担当が receive --> received
```

### 3.3 ユーザー招待・登録フロー

```
【管理者】                          【招待されたユーザー】
   |                                      |
   | 1. /admin/users で招待作成             |
   |    （ロール・メール・必要項目を入力）     |
   |    --> 招待メール送信（有効期限: 72時間）|
   |                                      |
   |                              2. メール受信
   |                                      |
   |                              3. /register?token=xxx にアクセス
   |                                      |
   |                              4. 氏名（必要なロールのみ）・パスワード設定
   |                                      |
   |                              5. アカウント作成完了
   |                                 --> 保護者は Assignment に parent_id が設定
   |                                 --> 保護者は既存報告書にも parent_id が反映
   |                                 --> 講師は tutor_no が自動採番済みで登録
   |                                      |
   |                              6. ログイン --> ロール別画面へ
```

保護者招待では担当講師と生徒名が必須。既存の保護者メールアドレスを指定した場合は、新規ユーザーを作らず既存保護者へ生徒を追加し、招待は受諾済みとして記録する。

---

## 4. 画面一覧

| URL | タイトル | アクセス可能ロール | 主な機能 |
|-----|---------|-----------------|---------|
| `/login` | ログイン | 全員（未認証） | メール・パスワードでログイン |
| `/register` | 保護者登録 | 未認証（招待トークン必須） | 招待トークンでパスワード設定・アカウント作成 |
| `/` | （リダイレクト） | 全ロール | 認証状態に応じて各画面へリダイレクト |
| `/dashboard` | ダッシュボード | 全ロール | ロールに応じたトップページへリダイレクト |
| `/tutor/reports` | 報告書一覧 | tutor | 当月の報告書一覧、新規作成、編集、チャット |
| `/tutor/reports/new` | 報告書新規作成 | tutor | 指導日・時間・科目・内容を入力 |
| `/tutor/reports/{id}` | 報告書詳細 | tutor | 報告書の内容確認・編集・チャット |
| `/tutor/submit` | 報告書一覧（互換ルート） | tutor | `/tutor/reports` と同じテンプレートを表示 |
| `/tutor/approval` | 承認管理 | tutor | 月次承認状況、保護者への一括依頼、差戻し再依頼、進捗ステッパー表示、エクスポート |
| `/parent/approval` | 承認管理 | parent | 報告書確認・承認・差戻し・操作履歴・エクスポート（1画面に統合） |
| `/parent/reports` | （廃止 → リダイレクト） | parent | `/parent/approval` へ 301 リダイレクト |
| `/parent/reports/{id}` | （廃止 → リダイレクト） | parent | `/parent/approval` へ 301 リダイレクト |
| `/admin/dashboard` | 運営ダッシュボード | admin_* | 生徒別カード、月・講師絞り込み、承認操作、エクスポート |
| `/admin/queue/receive` | 受付待ち一覧 | admin_receiver, admin_master | 受付待ち報告書の一覧・受付操作 |
| `/admin/queue/review` | 再鑑待ち一覧 | admin_reviewer, admin_master | 再鑑待ち報告書の一覧・再鑑操作 |
| `/admin/queue/approve` | 承認待ち一覧 | admin_master | 最終承認待ちの一覧・承認操作 |
| `/admin/reports/{id}` | 報告書詳細（運営） | admin_* | 個別報告書の内容確認・操作 |
| `/admin/users` | ユーザー管理 | admin_master | 招待メール送信、招待一覧、ユーザー一覧、ユーザー有効化・無効化 |
| `/admin/assignments` | 紐付け管理 | admin_master | 既存生徒への講師追加、紐付け一覧、紐付け無効化 |

---

## 5. 報告書ステータス一覧

### ステータス定義

| ステータス値 | 日本語名 | 説明 |
|------------|---------|------|
| `draft` | 下書き | 講師が作成・編集中。保護者には未送信 |
| `awaiting_parent_approval` | 保護者承認待ち | 保護者へ承認依頼送信済み |
| `parent_approved` | 保護者承認済み | 保護者が承認した中間状態。現在の API では保護者承認直後に運営提出まで自動実行されるため、通常 UI では短時間のみ発生 |
| `submitted_to_admin` | 運営提出済み（受付待ち） | 運営へ提出済み。受付担当の処理待ち |
| `received` | 受付済み（再鑑待ち） | 受付担当が受付。再鑑者の処理待ち |
| `re_reviewed` | 再鑑済み（最終承認待ち） | 再鑑者が確認済み。管理者の最終承認待ち |
| `admin_approved` | 最終承認済み | 管理者が最終承認。月次確定（終端） |
| `returned_to_tutor` | 講師へ差戻し | 受付担当または保護者が差戻し。講師が修正対応 |
| `returned_to_receiver` | 受付へ差戻し | 再鑑者または管理者が差戻し。受付担当が再受付 |
| `closed` | クローズ | 運営スタッフが理由付きでクローズ（終端）。レコードは削除しない |

### ステータス遷移図

```
        [draft] <----------------------------------------+
       下書き                                             |
            | submit_to_parent (tutor)                   |
            v                                             |
[awaiting_parent_approval]                                |
 保護者承認待ち                                            |
            | parent_approve    parent_return (parent)-->  |
            v                                             |
    [parent_approved]                           [returned_to_tutor]
    保護者承認済み                                講師へ差戻し
            | submit_to_admin (tutor) / 自動       |
            v                               submit_to_parent (tutor)
  [submitted_to_admin]  <-return_from_receiver (admin_receiver)---+
  受付待ち                                                          |
            | receive (admin_receiver)  return_from_receiver ----> |（上記へ）
            |                          (submitted_to_admin/received/returned_to_receiver) → returned_to_tutor
            v                                                       |
       [received]                                                   |
       受付済み                                                      |
            | re_review (admin_reviewer)  return_from_reviewer ---> [returned_to_receiver]
            v                                                               |
      [re_reviewed]                                             receive (admin_receiver)
      再鑑済み                                                              |
            | admin_approve (admin_master)  return_from_master --> [returned_to_receiver]
            v                                                               |
    [admin_approved]  return_from_master (admin_master) ----------> [returned_to_receiver]
    最終承認済み（終端）

                                [closed]（終端: 理由付きクローズ）
```

**差戻し先まとめ**

| 差戻しアクション | 発生元ステータス | 差戻し先 |
|----------------|---------------|---------|
| `parent_return` | awaiting_parent_approval | returned_to_tutor |
| `return_from_receiver` | submitted_to_admin / received / returned_to_receiver | returned_to_tutor |
| `return_from_reviewer` | received / re_reviewed | returned_to_receiver |
| `return_from_master` | re_reviewed / admin_approved | returned_to_receiver |

`returned_to_receiver` 状態の報告書は、受付担当が `receive` アクションで再受付できる。

### 各ステータスで可能なアクション

| ステータス | 可能なアクション | 実行ロール | 遷移先 |
|-----------|----------------|-----------|-------|
| draft | submit_to_parent | tutor | awaiting_parent_approval |
| draft | 編集, 削除 | tutor | — |
| awaiting_parent_approval | parent_approve | parent | parent_approved（→即座に submitted_to_admin へ自動提出） |
| awaiting_parent_approval | parent_return | parent | returned_to_tutor |
| submitted_to_admin | receive | admin_receiver, admin_master | received |
| submitted_to_admin | return_from_receiver | admin_receiver, admin_master | returned_to_tutor |
| received | re_review | admin_reviewer, admin_master | re_reviewed |
| received | return_from_receiver | admin_receiver, admin_master | returned_to_tutor |
| received | return_from_reviewer | admin_reviewer, admin_master | returned_to_receiver |
| re_reviewed | admin_approve | admin_master | admin_approved |
| re_reviewed | return_from_reviewer | admin_reviewer, admin_master | returned_to_receiver |
| re_reviewed | return_from_master | admin_master | returned_to_receiver |
| admin_approved | return_from_master | admin_master | returned_to_receiver |
| returned_to_tutor | submit_to_parent, 編集 | tutor | awaiting_parent_approval |
| returned_to_receiver | receive | admin_receiver, admin_master | received |
| returned_to_receiver | return_from_receiver | admin_receiver, admin_master | returned_to_tutor |
| 任意（終端以外・先月以前） | close（理由必須） | admin_receiver, admin_reviewer, admin_master | closed |

---

## 6. API仕様

認証は JWT トークンを httpOnly Cookie（`access_token`）で保持する。一部エンドポイントは `Authorization: Bearer <token>` ヘッダーも受け付ける。

### 認証 API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/auth/login` | 不要 | ログイン。Cookie に JWT を設定 |
| POST | `/api/auth/logout` | 不要 | ログアウト。Cookie を削除 |
| POST | `/api/auth/select-role` | ログイン済み | 複数ロール保有時の使用ロール選択 |
| GET | `/api/auth/me` | ログイン済み | 現在ユーザー情報取得 |
| GET | `/api/auth/register` | 不要（token 必須） | 招待トークン情報取得（メール・生徒名） |
| POST | `/api/auth/register` | 不要（token 必須） | 招待ロールに応じたアカウント作成 |
| POST | `/api/auth/forgot-password` | 不要 | パスワードリセットメール送信。存在しないメールでも同じ成功レスポンスを返す |
| GET | `/api/auth/reset-password` | 不要（token 必須） | パスワードリセットトークンの有効性確認 |
| POST | `/api/auth/reset-password` | 不要（token 必須） | 新しいパスワード設定 |

**POST /api/auth/login リクエスト（form-data）**
```
username: メールアドレス
password: パスワード
```

**POST /api/auth/login レスポンス**
```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "role": "tutor",
  "display_name": "山田 太郎"
}
```

### パスワードリセット仕様

パスワードリセットはメールリンク方式で行う。利用者は `/forgot-password` でメールアドレスを入力し、受信した `{BASE_URL}/reset-password?token=...` から新しいパスワードを設定する。

| 項目 | 内容 |
|------|------|
| トークン生成 | `secrets.token_urlsafe(32)` |
| 有効期限 | 発行から1時間 |
| 保存先 | `password_reset_tokens` テーブル |
| 再利用防止 | 使用後に `used_at` を設定し、以降は 409 を返す |
| 存在しないメール | アカウント有無を推測できないよう成功レスポンスを返す |
| パスワード条件 | 8文字以上 |

**POST /api/auth/forgot-password リクエスト**
```json
{
  "email": "user@example.com"
}
```

**GET /api/auth/reset-password レスポンス**
```json
{
  "valid": true,
  "email": "user@example.com",
  "reason": null
}
```

無効な場合の `reason` は `expired` / `used` / `not_found` のいずれか。

**POST /api/auth/reset-password リクエスト**
```json
{
  "token": "xxxxx",
  "new_password": "NewPassw0rd!"
}
```

### ユーザー管理 API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/users` | admin_master | ユーザー作成（API 直接利用時。UI は招待方式を使用） |
| GET | `/api/users` | admin_* | ユーザー一覧（`?role=tutor` 等で絞り込み可） |
| GET | `/api/users/{user_id}` | admin_* | ユーザー取得 |
| PATCH | `/api/users/{user_id}` | admin_master | ユーザー情報更新 |
| POST | `/api/users/me/password` | ログイン済み | 自分のパスワード変更 |
| POST | `/api/users/{user_id}/reset-password` | admin_master | パスワードリセット（新パスワードを返却） |

### 担当紐付け API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/assignments` | tutor（自分のみ）, admin_master | 担当紐付け作成 |
| GET | `/api/assignments` | ログイン済み（ロール別フィルタ） | 紐付け一覧 |
| PATCH | `/api/assignments/{assignment_id}` | admin_master | 紐付け更新（parent_id 変更時は既存報告書にも反映） |

### 招待管理 API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/invitations` | admin_master | ユーザー招待作成・メール送信（有効期限72時間）。parent / tutor / admin_receiver / admin_reviewer / admin_master に対応 |
| GET | `/api/invitations` | admin_master | 招待一覧 |
| DELETE | `/api/invitations/{invitation_id}` | admin_master | 招待取消（受諾済みは取消不可） |

**POST /api/invitations リクエスト**
```json
{
  "email": "new-user@example.com",
  "role": "parent",
  "display_name": "山田 太郎",
  "tutor_id": "uuid",
  "student_name": "田中 花子"
}
```

※ `parent` は `tutor_id` と `student_name` が必須。`tutor` は講師番号を自動採番する。運営スタッフと講師は登録画面で氏名を入力できる。

### 報告書 API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/reports` | tutor | 報告書作成（当月のみ、最終承認済み月は追加不可） |
| GET | `/api/reports` | ログイン済み（ロール別フィルタ） | 報告書一覧 |
| GET | `/api/reports/monthly-summary` | tutor, admin_* | 月次サマリー（フェーズ・合計時間等） |
| GET | `/api/reports/export` | tutor（担当のみ）, parent（自分の子のみ）, admin_*（全件） | Excel/CSV エクスポート。`assignment_id` 未指定時は複数生徒の一括出力 |
| GET | `/api/reports/{report_id}` | ロール別権限チェック | 報告書取得 |
| PATCH | `/api/reports/{report_id}` | tutor（下書き・差戻しのみ、当月のみ） | 報告書更新 |
| DELETE | `/api/reports/{report_id}` | tutor（下書きのみ） | 報告書削除 |

**GET /api/reports クエリパラメータ**

| パラメータ | 型 | 説明 |
|-----------|------|------|
| `status` | string | ステータスで絞り込み |
| `target_month` | string | 対象月（YYYY-MM）で絞り込み |
| `assignment_id` | UUID | 担当紐付けで絞り込み |
| `tutor_id` | UUID | 講師で絞り込み（admin_* のみ有効） |
| `parent_id` | UUID | 保護者で絞り込み（admin_* のみ有効） |

**GET /api/reports/export クエリパラメータ**

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `assignment_id` | O | 担当紐付け UUID |
| `target_month` | O | 対象月（YYYY-MM 形式） |
| `format` | X | `xlsx`（デフォルト）または `csv` |
| `scope` | X | `all` の場合、権限内の全生徒を一括出力 |
| `tutor_id` | X | 講師単位の一括出力（admin_* または本人 tutor） |

**POST /api/reports リクエスト**
```json
{
  "assignment_id": "uuid",
  "lesson_date": "2026-05-10",
  "start_time": "18:00:00",
  "end_time": "19:30:00",
  "break_minutes": 0,
  "subject": "数学",
  "content": "二次方程式の解法を学習..."
}
```

**ReportOut（レスポンス共通形式）**
```json
{
  "id": "uuid",
  "assignment_id": "uuid",
  "tutor_id": "uuid",
  "parent_id": "uuid",
  "student_name": "田中 花子",
  "lesson_date": "2026-05-10",
  "start_time": "18:00:00",
  "end_time": "19:30:00",
  "break_minutes": 0,
  "subject": "数学",
  "content": "...",
  "status": "admin_approved",
  "target_month": "2026-05",
  "submitted_to_parent_at": "2026-05-11T10:00:00Z",
  "parent_approved_at": "2026-05-12T08:00:00Z",
  "submitted_to_admin_at": "2026-05-13T09:00:00Z",
  "received_at": null,
  "re_reviewed_at": null,
  "admin_approved_at": null,
  "stale_since": null,
  "closed_at": null,
  "closed_by": null,
  "closed_by_name": null,
  "close_reason": null,
  "created_at": "2026-05-10T20:00:00Z",
  "updated_at": "2026-05-10T20:00:00Z",
  "last_event": "submit_to_parent",
  "last_return_comment": null,
  "last_return_at": null,
  "unread_count": 0,
  "events": []
}
```

### ワークフロー API（個別操作）

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/reports/{id}/submit-to-parent` | tutor | 保護者へ承認依頼（parent が呼ぶと差戻しキャンセル） |
| POST | `/api/reports/{id}/parent-approve` | parent | 保護者承認 |
| POST | `/api/reports/{id}/parent-return` | parent | 保護者差戻し（comment 必須） |
| POST | `/api/reports/{id}/submit-to-admin` | tutor | 運営へ提出 |
| POST | `/api/reports/{id}/receive` | admin_receiver, admin_master | 受付 |
| POST | `/api/reports/{id}/return-from-receiver` | admin_receiver, admin_master | 受付担当差戻し（comment 必須） |
| POST | `/api/reports/{id}/re-review` | admin_reviewer, admin_master | 再鑑 |
| POST | `/api/reports/{id}/return-from-reviewer` | admin_reviewer, admin_master | 再鑑者差戻し（comment 必須） |
| POST | `/api/reports/{id}/admin-approve` | admin_master | 最終承認 |
| POST | `/api/reports/{id}/return-from-master` | admin_master | 管理者差戻し（comment 必須） |

### ワークフロー API（一括操作）

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/reports/submit-to-parent-bulk` | tutor | 一括保護者依頼 |
| POST | `/api/reports/parent-approve-bulk` | parent | 一括保護者承認 |
| POST | `/api/reports/parent-return-bulk` | parent | 一括保護者差戻し |
| POST | `/api/reports/submit-to-admin-bulk` | tutor | 一括運営提出 |
| POST | `/api/reports/admin-receive-bulk` | admin_receiver, admin_master | 一括受付 |
| POST | `/api/reports/admin-review-bulk` | admin_reviewer, admin_master | 一括再鑑 |
| POST | `/api/reports/admin-approve-bulk` | admin_master | 一括最終承認 |
| POST | `/api/reports/admin-return-bulk` | admin_* | 一括差戻し（from_role 指定必須） |

**一括操作リクエスト（BulkSubmitIn）**
```json
{
  "report_ids": ["uuid1", "uuid2"],
  "target_month": "2026-05"
}
```

**一括差戻しリクエスト（AdminBulkReturnIn）**
```json
{
  "report_ids": ["uuid1", "uuid2"],
  "target_month": "2026-05",
  "from_role": "receiver",
  "comment": "差戻し理由テキスト"
}
```

※ `from_role` は `receiver` / `reviewer` / `master` のいずれか

### 未処理報告クローズ API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| GET | `/api/stale-count` | ログイン済み | ロール別未処理件数取得 |
| GET | `/api/stale-reports` | admin_receiver, admin_reviewer, admin_master | 未処理報告書一覧（先月以前 + 終端以外） |
| POST | `/api/reports/{report_id}/close` | admin_receiver, admin_reviewer, admin_master | 報告書をクローズ（close_reason 必須） |

**POST /api/reports/{report_id}/close リクエスト**
```json
{
  "close_reason": "保護者と連絡が取れないためクローズ"
}
```

### チャット API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| GET | `/api/reports/{id}/messages` | tutor, parent, admin_* | メッセージ一覧取得 |
| POST | `/api/reports/{id}/messages` | tutor, parent, admin_* | メッセージ送信（最大2000文字） |
| POST | `/api/reports/{id}/messages/{msg_id}/read` | ログイン済み | 既読マーク |

---

## 7. データモデル

### users テーブル

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | ユーザーID |
| email | VARCHAR(255) | UNIQUE, INDEX, NOT NULL | メールアドレス |
| password_hash | VARCHAR(255) | NOT NULL | bcrypt ハッシュ |
| role | VARCHAR(32) | INDEX, NOT NULL | 主ロール: tutor / parent / admin_receiver / admin_reviewer / admin_master（新システムは school / office / sales / admin_master も使用） |
| roles | JSON | NULL | 複数ロール保有時のロール配列 |
| display_name | VARCHAR(100) | NOT NULL | 表示名 |
| tutor_no | VARCHAR(20) | NULL | 講師番号（tutor のみ使用） |
| phone | VARCHAR(20) | NULL | 電話番号 |
| is_active | BOOLEAN | NOT NULL, default=True | 有効フラグ |
| deleted_at | TIMESTAMP WITH TZ | NULL | 論理削除日時（ソフトデリート） |
| user_no | VARCHAR(20) | NULL | 新システムのユーザー番号（T/S/X 番号帯）※新システムで追加 |
| allowed_systems | JSON | NULL | アクセス可能システムの配列 ※新システムで追加 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |
| updated_at | TIMESTAMP WITH TZ | NOT NULL | 更新日時 |

### assignments テーブル（担当紐付け）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 紐付けID |
| tutor_id | UUID | FK(users.id), INDEX, NOT NULL | 講師ID |
| parent_id | UUID | FK(users.id), INDEX, NULL | 保護者ID（招待受諾後に設定。新システムでは学校ID） |
| student_name | VARCHAR(100) | NOT NULL | 生徒名（新システムでは学校名） |
| is_active | BOOLEAN | NOT NULL, default=True | 有効フラグ |
| skip_parent_approval | BOOLEAN | NOT NULL, default=False | 保護者承認スキップ（新システムでは学校承認スキップに転用） |
| reminder_enabled | BOOLEAN | NOT NULL, default=False | リマインダー有効 |
| reminder_days_after | INTEGER | NOT NULL, default=1 | リマインダー間隔（日） |
| reminder_count | INTEGER | NOT NULL, default=1 | リマインダー最大回数 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |
| system_type | VARCHAR(10) | NULL, default='legacy' | 所属システム（'legacy' / 'work'）※新システムで追加 |

### lesson_reports テーブル（報告書）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 報告書ID |
| assignment_id | UUID | FK(assignments.id), INDEX, NOT NULL | 担当紐付けID |
| tutor_id | UUID | FK(users.id), INDEX, NOT NULL | 講師ID |
| parent_id | UUID | FK(users.id), INDEX, NULL | 保護者ID |
| lesson_date | DATE | NOT NULL | 指導日 |
| start_time | TIME | NOT NULL | 開始時刻 |
| end_time | TIME | NOT NULL | 終了時刻（start_time より後） |
| break_minutes | INTEGER | NOT NULL, default=0 | 休憩時間（分） |
| subject | VARCHAR(100) | NULL | 科目（任意） |
| content | TEXT | NOT NULL | 指導内容（最大2000文字） |
| status | VARCHAR(32) | INDEX, NOT NULL | ステータス値（§5参照） |
| target_month | VARCHAR(7) | INDEX, NOT NULL | 対象月（YYYY-MM 形式） |
| submitted_to_parent_at | TIMESTAMP WITH TZ | NULL | 保護者へ送信日時 |
| parent_approved_at | TIMESTAMP WITH TZ | NULL | 保護者承認日時 |
| submitted_to_admin_at | TIMESTAMP WITH TZ | NULL | 運営提出日時 |
| received_at | TIMESTAMP WITH TZ | NULL | 受付日時 |
| re_reviewed_at | TIMESTAMP WITH TZ | NULL | 再鑑日時 |
| admin_approved_at | TIMESTAMP WITH TZ | NULL | 最終承認日時 |
| stale_since | TIMESTAMP WITH TZ | NULL | 未処理判定日時（バッチが初回検出した時刻） |
| closed_at | TIMESTAMP WITH TZ | NULL | クローズ日時 |
| closed_by | UUID | FK(users.id), NULL | クローズ実行者ID |
| close_reason | VARCHAR(500) | NULL | クローズ理由（必須、最大500文字） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |
| updated_at | TIMESTAMP WITH TZ | NOT NULL | 更新日時 |

### report_events テーブル（操作履歴・監査ログ）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | イベントID |
| report_id | UUID | FK(lesson_reports.id), INDEX, NOT NULL | 報告書ID |
| actor_id | UUID | FK(users.id), INDEX, NOT NULL | 操作ユーザーID |
| action | VARCHAR(32) | NOT NULL | アクション名 |
| from_status | VARCHAR(32) | NULL | 遷移前ステータス |
| to_status | VARCHAR(32) | NULL | 遷移後ステータス |
| comment | TEXT | NULL | 差戻しコメント等 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 発生日時 |

**アクション値一覧**: `create`, `update`, `submit_to_parent`, `parent_approve`, `parent_return`, `parent_return_cancel`, `submit_to_admin`, `receive`, `return_from_receiver`, `re_review`, `return_from_reviewer`, `admin_approve`, `return_from_master`

### invitations テーブル

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 招待ID |
| email | VARCHAR(255) | INDEX, NOT NULL | 招待先メールアドレス |
| role | VARCHAR(32) | NOT NULL, default='parent' | 招待ロール（parent / tutor / admin_receiver / admin_reviewer / admin_master） |
| display_name | VARCHAR(100) | NULL | 招待時の氏名初期値（講師・運営スタッフ用） |
| tutor_no | VARCHAR(20) | NULL | 招待時に自動採番した講師番号（tutor 用） |
| assignment_id | UUID | FK(assignments.id), INDEX, NULL | 紐付けID |
| token | VARCHAR(128) | UNIQUE, INDEX, NOT NULL | 招待トークン（urlsafe 32byte） |
| invited_by | UUID | FK(users.id), NULL | 招待した管理者ID |
| expires_at | TIMESTAMP WITH TZ | NOT NULL | 有効期限（作成から72時間） |
| accepted_at | TIMESTAMP WITH TZ | NULL | 受諾日時（未受諾は NULL） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |

### password_reset_tokens テーブル

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | パスワードリセットトークンID |
| user_id | UUID | FK(users.id), INDEX, NOT NULL | 対象ユーザーID |
| token | VARCHAR(128) | UNIQUE, INDEX, NOT NULL | リセットトークン（urlsafe 32byte） |
| expires_at | TIMESTAMP WITH TZ | NOT NULL | 有効期限（作成から1時間） |
| used_at | TIMESTAMP WITH TZ | NULL | 使用日時（未使用は NULL） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |

### chat_messages テーブル

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | メッセージID |
| report_id | UUID | FK(lesson_reports.id), INDEX, NOT NULL | 報告書ID |
| sender_id | UUID | FK(users.id), INDEX, NOT NULL | 送信者ID |
| body | TEXT | NOT NULL | 本文（最大2000文字） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 送信日時 |

### chat_reads テーブル（既読管理）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| message_id | UUID | FK(chat_messages.id), PK | メッセージID |
| user_id | UUID | FK(users.id), PK | 既読ユーザーID |
| read_at | TIMESTAMP WITH TZ | NOT NULL | 既読日時 |

※ `(message_id, user_id)` に UNIQUE 制約あり

### notifications テーブル

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 通知ID |
| user_id | UUID | FK(users.id), INDEX, NOT NULL | 宛先ユーザーID |
| report_id | UUID | FK(lesson_reports.id), INDEX, NULL | 関連報告書ID |
| channel | VARCHAR(16) | NOT NULL, default='email' | チャネル（現在は email のみ） |
| type | VARCHAR(32) | NOT NULL | 通知種別 |
| subject | VARCHAR(255) | NOT NULL | 件名 |
| body | TEXT | NOT NULL | 本文 |
| sent_at | TIMESTAMP WITH TZ | NULL | 送信日時 |
| read_at | TIMESTAMP WITH TZ | NULL | 既読日時 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |

### テーブル間リレーション

```
users
 +-(tutor_id)---> assignments <-(parent_id)- users
 |                    |
 |           (assignment_id)
 |                    v
 +-(tutor_id, parent_id, closed_by)--> lesson_reports
                                              |
                           +-----------------+--------------+
                           v                 v               v
                   report_events       chat_messages    notifications
                  (actor_id->users)  (sender_id->users)
                                           |
                                      chat_reads
                                    (user_id->users)

assignments --(assignment_id)--> invitations
                                  (invited_by->users)

users --(user_id)--> password_reset_tokens
```

---

## 8. 未処理報告クローズ機能

### 概要

先月以前の報告書で終端ステータス（`admin_approved` / `closed`）に到達していないものを「未処理報告」とみなし、運営スタッフが理由を付けてクローズできる機能。自動クローズは行わず、判断は必ず人間が行う。

### 対象条件

| 条件 | 内容 |
|------|------|
| 対象月 | `target_month < 当月`（先月以前） |
| 対象ステータス | `admin_approved` / `closed` 以外のすべてのステータス |

### 操作権限

| ロール | 操作 |
|--------|------|
| admin_receiver | 未処理一覧閲覧・クローズ |
| admin_reviewer | 未処理一覧閲覧・クローズ |
| admin_master | 未処理一覧閲覧・クローズ |
| tutor / parent | 自分に関係する件数をバナーで確認のみ（クローズ不可） |

### クローズ処理

- `close_reason`（最大500文字）が必須。空文字や空白のみは拒否
- レコードは削除しない。`status = closed`、`closed_at`、`closed_by`、`close_reason` を記録
- クローズ後も同月の新規報告書作成は可能

### バナー表示

全画面のヘッダー領域に件数バナーを表示する。ロール別フィルタにより、自分に関係する未処理件数のみが表示される。

### stale_since と エスカレーション通知

- バッチが未処理報告を初回検出した時刻を `stale_since` に記録（以降は上書きしない）
- `stale_since` からの経過日数に応じて 7日・14日・30日でエスカレーション通知を送信

---

## 9. 通知仕様

### メール通知一覧

| トリガーアクション | 件名 | 送信先 | テンプレート |
|------------------|------|--------|-------------|
| `submit_to_parent` | 【指導実績】承認依頼が届きました | 保護者 | `notify_approval_request.txt` |
| `parent_return` | 【指導実績】差戻しコメントが届きました | 講師 | `notify_returned.txt` |
| `parent_approve` | 【指導実績】保護者が承認しました | 講師 | `notify_parent_approved.txt` |
| `submit_to_admin` | 【指導実績】報告書が提出されました | admin_receiver 全員 | `notify_submitted_to_admin.txt` |
| `return_from_receiver` | 【指導実績】運営から差戻しがありました | 講師 | `notify_returned.txt` |
| `return_from_reviewer` | 【指導実績】運営から差戻しがありました | 講師 | `notify_returned.txt` |
| `return_from_master` | 【指導実績】運営から差戻しがありました | 講師 | `notify_returned.txt` |
| `admin_approve` | 【指導実績】最終承認が完了しました | 講師・保護者（両方） | `notify_admin_approved.txt` |
| 保護者招待作成 | 【指導実績報告システム】保護者アカウントのご案内 | 招待先メールアドレス | `email/invitation.txt` |
| 講師招待作成 | 【指導実績報告システム】講師アカウントのご案内 | 招待先メールアドレス | `email/invitation_tutor.txt` |
| 運営スタッフ招待作成 | 【指導実績報告システム】スタッフアカウントのご案内 | 招待先メールアドレス | `email/invitation_staff.txt` |
| パスワードリセット依頼 | 【指導実績報告システム】パスワードリセットのご案内 | 対象ユーザー | `email/password_reset.txt` |

`transition()` では上記メールとは別に、状態変更時の監査・将来表示用として `notifications` テーブルへ `status_changed` レコードを作成する。

### 月末リマインダー通知

APScheduler によって毎日 **09:00 JST** に自動実行される。
月末 `REMINDER_DAYS_BEFORE_MONTH_END`（デフォルト: **3**）日前以降の日に発火する。

| 条件 | 送信先 | 通知種別 |
|------|--------|---------|
| `awaiting_parent_approval` の報告書が存在 | 保護者 | `reminder_unapproved` |
| `draft` / `returned_to_tutor` / `parent_approved` の報告書が存在 | 講師 | `reminder_unsubmitted` |

### メール通知のコンテキスト変数

| 変数名 | 内容 |
|--------|------|
| `base_url` | システムの公開 URL |
| `target_month` | 対象月（YYYY-MM） |
| `student_name` | 生徒名 |
| `count` | 対象報告書件数 |
| `total_hours` | 合計指導時間（例: 3時間30分） |
| `tutor_name` | 講師の表示名 |
| `parent_name` | 保護者の表示名 |
| `actor_name` | 差戻し実行者の表示名（差戻し系通知のみ） |
| `comment` | 差戻しコメント（差戻し系通知のみ） |
| `lesson_date` | 指導日（差戻し系通知のみ） |

---

## 10. エクスポート機能

### エクスポートできる条件

| ロール | 条件 |
|--------|------|
| tutor（講師） | 自分が担当する assignment の報告書が1件以上あること。UI 上は **最終承認済み（admin_approved）** の生徒・月のみ生徒別ボタンを表示し、全生徒一括は対象月の全生徒が最終承認済みの場合のみ有効 |
| parent（保護者） | 表示月のステータスが `admin_approved` の場合のみ PDF エクスポートボタンを表示 |
| admin_*（運営） | 対象 assignment または対象月の報告書が1件以上あれば **ステータス不問** で生徒別・全体一括ダウンロード可能 |

### ファイル形式

| 形式 | 拡張子 | MIME Type | 文字コード | 特徴 |
|------|--------|-----------|-----------|------|
| Excel | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | - | ヘッダー行：太字・薄グレー背景。合計行：太字。列幅：文字数に応じて自動調整（最大60）。シート名：`N月指導実績` |
| CSV | `.csv` | `text/csv; charset=utf-8-sig` | UTF-8 BOM 付き | Python 標準 csv モジュールで生成。Excel で開いても文字化けしない |

### ファイル名

```
指導実績_{生徒名}_{YYYY年MM月}.xlsx
指導実績_{生徒名}_{YYYY年MM月}.csv
```

例: `指導実績_田中花子_2026年05月.xlsx`

### 列構成

| 列名 | 内容 | 形式例 |
|------|------|-------|
| 回数 | 連番（1始まり） | 1, 2, 3... |
| 指導日 | M月D日（曜日） | 5月10日（金） |
| 開始時刻 | HH:MM | 18:00 |
| 終了時刻 | HH:MM | 19:30 |
| 休憩（分） | 整数（分） | 0 |
| 指導時間 | X時間Y分 | 1時間30分 |
| 科目 | 科目名（未設定時は空） | 数学 |
| 指導内容 | 全文 | 二次方程式の解法を... |
| ステータス | 日本語ステータス名 | 最終承認済み |

複数生徒を一括出力する場合、CSV は先頭列に「生徒名」を追加する。Excel は「全体サマリ」シートと生徒別シートを作成する。

### 最終行（合計行）

```
合計指導時間：○時間○分
```

Excel ではすべての列にわたってセルを結合し、太字で表示する。

---

## 11. 環境構成

### 技術スタック

| レイヤー | 技術 | バージョン |
|---------|------|----------|
| 言語 | Python | 3.11+ |
| Web フレームワーク | FastAPI | 0.115.2 |
| ASGI サーバー | Uvicorn（standard extras） | 0.27+ |
| ORM | SQLAlchemy | 2.0+ |
| DB マイグレーション | Alembic | 1.13+ |
| DB ドライバー | psycopg v3（binary） | 3.1+ |
| バリデーション | Pydantic / pydantic-settings | 2.6+ / 2.2+ |
| 認証 | python-jose + bcrypt | 3.3+ / 4.0.1 |
| テンプレートエンジン | Jinja2 | 3.1+ |
| CSS フレームワーク | Tailwind CSS（CDN） | latest |
| SMTP クライアント | aiosmtplib | 3.0+ |
| タスクスケジューラー | APScheduler | 3.10+ |
| Excel 生成 | openpyxl | 3.1+ |
| HTTP テストクライアント | httpx | 0.27+ |
| テストフレームワーク | pytest / pytest-asyncio / freezegun | 8.0+ / 0.23+ / 1.4+ |
| データベース | PostgreSQL | 16 |
| 開発用 SMTP サーバー | MailHog | 1.0.1 |
| コンテナ | Docker / Docker Compose | - |

### 環境変数一覧（.env.example より）

| 変数名 | デフォルト値 | 必須 | 説明 |
|--------|------------|:----:|------|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@db:5432/tutor` | O | PostgreSQL 接続文字列 |
| `JWT_SECRET` | `change-me-in-production` | O | JWT 署名シークレット（本番は必ず変更） |
| `JWT_ALGORITHM` | `HS256` | | JWT アルゴリズム |
| `ACCESS_TOKEN_EXPIRE_HOURS` | `8` | | トークン有効時間（時間） |
| `SMTP_HOST` | `mailhog` | | SMTP ホスト |
| `SMTP_PORT` | `1025` | | SMTP ポート |
| `SMTP_USERNAME` | （空） | | SMTP 認証ユーザー名 |
| `SMTP_PASSWORD` | （空） | | SMTP 認証パスワード |
| `SMTP_FROM` | `noreply@example.com` | | 送信元メールアドレス |
| `BASE_URL` | `http://localhost:8000` | O | システム公開 URL（招待メールの URL 生成に使用） |
| `REMINDER_DAYS_BEFORE_MONTH_END` | `3` | | 月末リマインダーの発火タイミング（末日から何日前か） |
| `TIMEZONE` | `Asia/Tokyo` | | アプリケーションのタイムゾーン |
| `CORS_ORIGINS` | （空） | | CORS 許可オリジン（カンマ区切り） |
| `AUTO_CREATE_TABLES` | `false` | | 起動時に自動テーブル作成（通常は Alembic で管理） |
| `ENVIRONMENT` | `development` | | 実行環境（development / production） |

### Docker Compose 構成

| サービス | イメージ | ポート | 役割 |
|---------|---------|-------|------|
| `db` | postgres:16-alpine | 5432（内部） | PostgreSQL データベース。postgres-data Volume に永続化 |
| `mailhog` | mailhog/mailhog:v1.0.1 | 1025（SMTP）, 8025（Web UI） | 開発用 SMTP サーバー |
| `backend` | ./backend/Dockerfile（ビルド） | 8000:8000 | FastAPI アプリケーション |

**アクセス URL（開発環境）**

| サービス | URL |
|---------|-----|
| アプリケーション | http://localhost:8000 |
| API ドキュメント（Swagger） | http://localhost:8000/docs |
| API ドキュメント（ReDoc） | http://localhost:8000/redoc |
| MailHog Web UI | http://localhost:8025 |

### インフラ構成情報

AWS Lightsail 環境、静的IP、公開URL、SSH接続方法、本番化前の作業、サーバー更新手順は [INFRASTRUCTURE.md](INFRASTRUCTURE.md) を参照。

---

## 12. 運用手順

### 初回起動

```bash
# 1. リポジトリクローン
git clone https://github.com/s-ohashi2/tutor-report-system.git
cd tutor-report-system

# 2. 環境変数ファイル作成
cp .env.example .env
# .env の JWT_SECRET を必ず変更すること

# 3. ビルド＆起動
docker compose up -d --build

# 4. 動作確認
# http://localhost:8000 --> ログイン画面が表示されることを確認
```

### 通常の起動・停止

```bash
# 起動
docker compose up -d

# 停止（データは保持される）
docker compose down

# ログ確認
docker compose logs backend -f

# 完全削除（データも消える）
docker compose down -v
```

### 依存パッケージ追加後の再ビルド

```bash
# pyproject.toml 変更後は必ず --build が必要
docker compose down && docker compose up -d --build
```

### 開発用リセット手順

```bash
# DB データを初期状態に戻す（開発環境のみ）
docker compose exec backend python app/scripts/dev_reset.py
```

### 新規ユーザー追加手順

#### ユーザー追加（招待方式）

1. admin_master アカウントで `/admin/users` にアクセス
2. 新規ユーザー登録フォームでロールを選択
3. 講師・運営スタッフは氏名とメールアドレスを入力
4. 保護者はメールアドレス・担当講師・生徒名を入力
5. 招待メールを送信（**有効期限: 72時間**）
6. 招待されたユーザーが `{BASE_URL}/register?token=xxx` でパスワードを設定
7. 保護者の場合は担当紐付け（assignment）の parent_id が自動確定し、既存報告書にも反映される

未受諾の招待は `/admin/users` から再送または取消できる。受諾済み招待は取消不可。登録済みユーザーは同画面で有効化・無効化できる。

### マイグレーション手順

```bash
# 現在の状態確認
docker compose exec backend alembic current

# 最新へ適用
docker compose exec backend alembic upgrade head

# 1つ前にロールバック
docker compose exec backend alembic downgrade -1
```

### バックアップ手順（暫定）

定期バックアップは未実装。以下の手動手順で対応する。

```bash
# バックアップ取得
docker compose exec db pg_dump -U postgres tutor > backup_$(date +%Y%m%d_%H%M%S).sql

# リストア
docker compose exec -T db psql -U postgres tutor < backup_YYYYMMDD_HHMMSS.sql
```

---

## 13. 将来拡張予定

### LINE 通知連携

- 現在はメール通知のみ実装済み
- LINE Messaging API を使ったプッシュ通知を追加予定
- `notifications.channel` カラムが `email` / `line` の切り替えを想定した設計になっている

### SSO 認証

- 現在は独自 JWT 認証（メール＋パスワード）
- Google Workspace / Microsoft Entra ID との SSO 連携を検討
- FastAPI の OAuth2 機構を活用して拡張予定

### AWS 移管

- 現在は Docker Compose によるオンプレミス構成
- 想定移行先: Amazon ECS（Fargate）+ RDS PostgreSQL + Amazon SES + CloudFront
- `DATABASE_URL` / `SMTP_*` 環境変数の変更のみで対応可能な設計

### 外部マスタ連携

- 現在はユーザー・担当紐付けを画面から手動管理
- 人事システム・塾管理システムとの CSV / API 連携による自動同期を検討
