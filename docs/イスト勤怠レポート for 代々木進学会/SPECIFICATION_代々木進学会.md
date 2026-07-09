# イスト勤怠レポート for 代々木進学会 — 仕様書（SPECIFICATION）

> 旧称: 指導実績報告システム（既存システム）。`backend/`・ポート8000。
> 本書は2システム構成のうち「イスト勤怠レポート for 代々木進学会」の開発者向け仕様書です。
> 共通情報: データモデル `../DATA_MODEL.md` / インフラ `../INFRASTRUCTURE.md` / 引継ぎ `../HANDOFF.md` / 索引 `../README.md`
> 最終更新: 2026-06-22

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

**イスト勤怠レポート for 代々木進学会（旧称: 指導実績報告システム）** は、家庭教師の指導実績（指導日・時間・内容）を月次でデジタル記録し、保護者確認・運営承認の多段階ワークフローを経て最終確定するシステム。紙・メール・口頭による報告業務をシステム化し、承認状況の可視化・追跡を実現する。

### 対象ユーザー

| ユーザー | 役割の概要 |
|----------|-----------|
| 講師（tutor） | 指導実績を記録し、保護者・運営の承認を取り付ける |
| 保護者（parent） | 講師の報告書を確認し、承認または差戻しを行う |
| 受付担当（admin_receiver） | 運営へ提出された報告書を受け付ける |
| 再鑑者（admin_reviewer） | 受付済み報告書を再確認する。**再鑑の承認＝最終承認** |
| 管理者（admin_master） | 承認フロー外。閲覧・PDF・ユーザー/担当管理・未処理クローズを行う |
| 管理責任者（admin_chief） | admin_master の権限に加え、chief 専用設定（承認スキップ設定・chief 招待/運用）を行う。承認フロー外 |

> 承認フローに加わるのは tutor / parent / admin_receiver / admin_reviewer の4ロール。admin_master / admin_chief は承認フローの外にあり、最終承認は行わない（§2・§3 参照）。

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

> 本システムは新システム（業務連絡表 / `new_backend` / ポート8001）と同一 PostgreSQL（`tutor`）を共有する。テーブルの共有関係・全体像は `../DATA_MODEL.md` を参照。

---

## 2. 登場人物とロール

### ロール一覧と権限表

承認フローに加わるのは tutor / parent / admin_receiver / admin_reviewer の4ロール。admin_master / admin_chief は承認フロー外（閲覧・PDF・ユーザー/担当管理・未処理クローズ）。

| 操作 | tutor | parent | admin_receiver | admin_reviewer | admin_master | admin_chief |
|------|:-----:|:------:|:--------------:|:--------------:|:------------:|:-----------:|
| 報告書作成（当月のみ） | O | X | X | X | X | X |
| 報告書編集（下書き・差戻し） | O | X | X | X | X | X |
| 報告書削除（下書きのみ） | O | X | X | X | X | X |
| 報告書閲覧（自分の担当分） | O | O | X | X | X | X |
| 報告書閲覧（全件） | X | X | O | O | O | O |
| 保護者へ承認依頼送信 | O | X | X | X | X | X |
| 保護者承認・差戻し | X | O | X | X | X | X |
| 運営へ提出 | O | X | X | X | X | X |
| 受付（receive） | X | X | O | X | X | X |
| 再鑑＝最終承認（re_review） | X | X | X | O | X | X |
| 差戻し（受付担当から） | X | X | O | X | X | X |
| 差戻し（再鑑者から） | X | X | X | O | X | X |
| エクスポート（自分の担当分） | O | O | X | X | X | X |
| エクスポート（全件） | X | X | O | O | O | O |
| 未処理報告クローズ | X | X | O | O | O | O |
| ユーザー作成・管理 | X | X | O | O | O | O |
| ユーザー招待送信 | X | X | O | O | O | O |
| 保護者承認スキップ設定（ユーザー管理） | X | X | O | O | O | O |
| チャットメッセージ送受信 | O | O | O | O | O | O |

> 上表は実装（`backend/app/core/rbac.py`・`backend/app/api/workflow.py`・`backend/app/api/pages.py`）に準拠する。受付・再鑑承認はそれぞれ admin_receiver / admin_reviewer のみが実行でき、admin_master・admin_chief であっても受付・再鑑（最終承認）アクションは実行できない（後述の職務分掌・承認フロー外の扱いによる）。ユーザー/担当管理・未処理クローズは受付・再鑑・管理者・管理責任者が同等に利用できる。

### 補足

- `admin_chief` は `admin_master` の権限に加え、chief 専用設定（保護者承認スキップ設定・chief 招待/運用）を持つ
- `admin_master` / `admin_chief` は承認フローの外にあり、承認キュー（受付/再鑑/最終承認）の操作はできない。ダッシュボードは閲覧用として利用する
- **職務分掌（受付/再鑑の兼務禁止）**: ある「報告書」で受付（receive）を判断（承認・差戻し）したスタッフは、その同じ報告書の再鑑（re_review）を判断できない（逆も同様）。スコープは報告書単位で、別生徒・別月の報告書には影響しない。admin_master / admin_chief はこの制約の対象外
- `tutor` は自分が担当する assignment に紐づく報告書のみ操作可能
- `parent` は自分の子どもの assignment に紐づく報告書のみ閲覧・承認可能
- 報告書の作成は **当月分のみ** に限定される
- 同一 assignment 同一月に既に `admin_approved` の報告書がある場合は追加不可
- 同一 assignment 同一月に `awaiting_parent_approval` 以降（進行中）の報告書がある場合は追加不可
- `draft`・`returned_to_tutor`・`closed` のみの場合は追加作成可能（`closed` は終端だが同月への新規作成はブロックしない）

---

## 3. 業務フロー

### 3.1 指導実績登録から最終承認までの全フロー

UI 上の承認管理は、講師・保護者・運営の状態を月次単位でまとめて扱う。利用者向けには次の4ステップで表示・運用する。最終承認は **再鑑者（admin_reviewer）の再鑑** であり、これをもって月次確定（`admin_approved`）となる。

| ステップ | 担当 | 主な状態 | 実装上のステータス |
|---------|------|----------|------------------|
| 1. 記録 | 講師 | 当月の指導実績を登録・修正する | `draft`, `returned_to_tutor` |
| 2. 保護者依頼 | 講師 | 月内の対象報告書を保護者へまとめて送る | `awaiting_parent_approval` |
| 3. 保護者承認 | 保護者 | 月次で承認または差戻しする。承認時は運営へ自動提出される | `submitted_to_admin` |
| 4. 運営承認 | 運営（受付→再鑑） | 受付担当が受付、再鑑者が再鑑（＝最終承認） | `received`, `admin_approved` |

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
   |                        |  5. 再鑑者が再鑑＝最終承認 |
   |                        |     (admin_approved)   |
   | <-- メール通知（講師へ）--------------------------|
   | <-- メール通知（保護者へも）-----------------------|
   |                        |                        |
  完了（admin_approved）
```

> `re_reviewed`（再鑑済み・最終承認待ち）は旧フローの中間状態で、現在は新規には作られない。フロー変更前から残っている `re_reviewed` の報告書も、再鑑者が再鑑（re_review）アクションで `admin_approved` に最終化できる。

### 3.2 差戻しフロー

差戻しは3種類あり、差戻し先は差戻しロールによって異なる。差戻し時はコメント入力が必須で、チャットにも自動投稿される。

```
【差戻し種別・発生元・差戻し先】

保護者差戻し（parent_return）
  awaiting_parent_approval --> returned_to_tutor
  → 講師が修正して再提出

受付担当差戻し（return_from_receiver）
  submitted_to_admin / received / returned_to_receiver --> returned_to_tutor
  → 講師が修正して再提出

再鑑者差戻し（return_from_reviewer）
  received / re_reviewed / admin_approved --> returned_to_receiver
  → 受付担当が再受付

【差戻し後の対応】
returned_to_tutor   --> 講師が修正 --> submit_to_parent --> awaiting_parent_approval
returned_to_receiver --> 受付担当が receive --> received
```

> 完了（`admin_approved`）後の差戻しは、最終承認者である **再鑑者（admin_reviewer）** が `return_from_reviewer` で受付へ戻す。旧フローの管理者差戻し（`return_from_master`）は廃止されている。

### 3.3 ユーザー招待・登録フロー

```
【運営（受付/再鑑/管理者/管理責任者）】   【招待されたユーザー】
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
| `/register` | アカウント登録 | 未認証（招待トークン必須） | 招待トークンでパスワード設定・アカウント作成 |
| `/forgot-password` | パスワード再設定依頼 | 未認証 | リセットメール送信 |
| `/reset-password` | パスワード再設定 | 未認証（トークン必須） | 新パスワード設定 |
| `/change-password` | パスワード変更 | ログイン済み（要変更時） | 初回ログイン等での強制パスワード変更 |
| `/select-role` | ロール選択 | 複数ロール保有者 | 使用ロールの選択 |
| `/` | （リダイレクト） | 全ロール | 認証状態に応じて各画面へリダイレクト |
| `/dashboard` | ダッシュボード | 全ロール | ロールに応じたトップページへリダイレクト |
| `/tutor/reports` | 報告書一覧 | tutor | 当月の報告書一覧、新規作成、編集、チャット |
| `/tutor/reports/new` | 報告書新規作成 | tutor | 指導日・時間・科目・内容を入力 |
| `/tutor/reports/{id}` | 報告書詳細 | tutor | 報告書の内容確認・編集・チャット |
| `/tutor/submit` | 報告書一覧（互換ルート） | tutor | `/tutor/reports` と同じテンプレートを表示 |
| `/tutor/approval` | 承認管理 | tutor | 月次承認状況、保護者への一括依頼、差戻し再依頼、進捗ステッパー表示、エクスポート |
| `/parent/approval` | 承認管理 | parent | 報告書確認・承認・差戻し・操作履歴・エクスポート（1画面に統合） |
| `/parent/report-view` | 報告書（参照） | parent | 報告書の中身を参照し、その場で承認・差戻しを行う |
| `/parent/reports` | （廃止 → リダイレクト） | parent | `/parent/approval` へリダイレクト |
| `/parent/reports/{id}` | （廃止 → リダイレクト） | parent | `/parent/approval` へリダイレクト |
| `/admin/dashboard` | 運営ダッシュボード | admin_*（全運営ロール） | 生徒別カード、月・講師絞り込み、エクスポート。受付/再鑑は承認操作も可、管理者/管理責任者は閲覧用 |
| `/admin/queue/receive` | 受付待ち一覧 | admin_receiver | 受付待ち報告書の一覧・受付操作 |
| `/admin/queue/review` | 再鑑待ち一覧 | admin_reviewer | 再鑑待ち報告書の一覧・再鑑（＝最終承認）操作 |
| `/admin/report-view` | 報告書（参照） | admin_*（全運営ロール） | 報告書を参照。受付/再鑑は参照画面から承認・差戻しを実行、管理者/管理責任者は参照のみ |
| `/admin/stale-reports` | 未処理報告一覧 | admin_*（全運営ロール） | 未処理報告の一覧・クローズ |
| `/admin/reports/{id}` | 報告書詳細（運営） | admin_*（全運営ロール） | 個別報告書の内容確認 |
| `/admin/users` | ユーザー管理 | admin_receiver, admin_reviewer, admin_master, admin_chief | 招待メール送信、未登録招待と登録済みユーザーの統合一覧、有効化・無効化、保護者詳細での「保護者承認スキップ」設定 |
| `/admin/assignments` | （担当管理ルート） | admin_receiver, admin_reviewer, admin_master, admin_chief | 担当紐付け管理ルート（UI 上の主導線は講師の生徒管理／保護者招待） |

> `/admin/queue/approve` ルートは登録されているが、現行のアクセス制御ではどのロールにも許可されておらず、承認キューとしては利用されない（最終承認は再鑑者が `/admin/queue/review` で行う）。受付・再鑑はユーザー管理・担当管理を管理者と同等に利用できる。管理者・管理責任者は承認キューを持たず、ダッシュボード／参照画面は閲覧用となる。
>
> 旧「システム設定」画面（`/admin/assignments` の UI）は廃止。保護者承認スキップは `/admin/users` の保護者詳細ドロワーに移設、リマインドは講師の承認管理画面（`/tutor/approval`）で承認依頼時に設定する。担当紐付けは講師の生徒管理と保護者招待（`/admin/users`）で作成する。

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
| `re_reviewed` | 再鑑済み（旧・最終承認待ち） | **レガシー状態**。フロー変更前から残る報告書のみが取り得る。新規には発生せず、再鑑者が再鑑（最終承認）で `admin_approved` に確定できる |
| `admin_approved` | 最終承認済み | 再鑑者の再鑑＝最終承認により月次確定（終端） |
| `returned_to_tutor` | 講師へ差戻し | 受付担当または保護者が差戻し。講師が修正対応 |
| `returned_to_receiver` | 受付へ差戻し | 再鑑者が差戻し。受付担当が再受付 |
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
    [admin_approved]                                            receive (admin_receiver)
    最終承認済み（終端）                                                    |
            | return_from_reviewer (admin_reviewer) ----------------> [returned_to_receiver]
            v
    [returned_to_receiver]

                                [closed]（終端: 理由付きクローズ）

  ※ [re_reviewed]（旧・最終承認待ち）はレガシー状態。新規には発生せず、
    再鑑者が re_review で [admin_approved] に最終化、または return_from_reviewer で
    [returned_to_receiver] に差戻しできる。
```

**差戻し先まとめ**

| 差戻しアクション | 発生元ステータス | 差戻し先 |
|----------------|---------------|---------|
| `parent_return` | awaiting_parent_approval | returned_to_tutor |
| `return_from_receiver` | submitted_to_admin / received / returned_to_receiver | returned_to_tutor |
| `return_from_reviewer` | received / re_reviewed / admin_approved | returned_to_receiver |

`returned_to_receiver` 状態の報告書は、受付担当が `receive` アクションで再受付できる。旧フローの管理者差戻し（`return_from_master`）は廃止。

### 各ステータスで可能なアクション

| ステータス | 可能なアクション | 実行ロール | 遷移先 |
|-----------|----------------|-----------|-------|
| draft | submit_to_parent | tutor | awaiting_parent_approval |
| draft | 編集, 削除 | tutor | — |
| awaiting_parent_approval | parent_approve | parent | parent_approved（→即座に submitted_to_admin へ自動提出） |
| awaiting_parent_approval | parent_return | parent | returned_to_tutor |
| submitted_to_admin | receive | admin_receiver | received |
| submitted_to_admin | return_from_receiver | admin_receiver | returned_to_tutor |
| received | re_review（＝最終承認） | admin_reviewer | admin_approved |
| received | return_from_receiver | admin_receiver | returned_to_tutor |
| received | return_from_reviewer | admin_reviewer | returned_to_receiver |
| re_reviewed（レガシー） | re_review（＝最終承認） | admin_reviewer | admin_approved |
| re_reviewed（レガシー） | return_from_reviewer | admin_reviewer | returned_to_receiver |
| admin_approved | return_from_reviewer | admin_reviewer | returned_to_receiver |
| returned_to_tutor | submit_to_parent, 編集 | tutor | awaiting_parent_approval |
| returned_to_receiver | receive | admin_receiver | received |
| returned_to_receiver | return_from_receiver | admin_receiver | returned_to_tutor |
| 任意（終端以外・先月以前） | close（理由必須） | admin_receiver, admin_reviewer, admin_master, admin_chief | closed |

> 受付（receive）・再鑑（re_review）と、それぞれの差戻し（return_from_receiver / return_from_reviewer）は、`backend/app/services/workflow_service.py` の `TRANSITIONS` 上 admin_receiver / admin_reviewer のみに許可される。admin_master / admin_chief は承認フロー外のため、これらのアクションを実行できない（職務分掌チェックの対象外ではあるが、ロール要件で弾かれる）。

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

無効な場合の `reason` は `expired` / `used` / `not_found` のいずれか。

### ユーザー管理 API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/users` | admin_master | ユーザー作成（API 直接利用時。UI は招待方式を使用） |
| GET | `/api/users` | admin_* | ユーザー一覧（`?role=tutor` 等で絞り込み可） |
| GET | `/api/users/{user_id}` | admin_* | ユーザー取得 |
| PATCH | `/api/users/{user_id}` | admin_master | ユーザー情報更新（保護者の `skip_parent_approval`＝保護者承認スキップを含む） |
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
| GET | `/api/reports/export` | tutor（担当のみ）, parent（自分の子のみ）, admin_*（全件） | 指導時間確認票PDF。`assignment_id` 未指定時は複数生徒の一括出力 |
| GET | `/api/reports/export-daily` | `/export` と同一 | 指導日報PDF（原本様式・1ページ5日分・会員認め印つき）。対象選定も `/export` と共通 |
| GET | `/api/reports/{report_id}` | ロール別権限チェック | 報告書取得 |
| PATCH | `/api/reports/{report_id}` | tutor（下書き・差戻しのみ、当月のみ） | 報告書更新 |
| POST | `/api/reports/admin-edit-bulk` | admin_receiver 等（受付による明細修正） | 受付担当による報告書の一括編集 |
| DELETE | `/api/reports/{report_id}` | tutor（下書きのみ） | 報告書削除 |

**GET /api/reports クエリパラメータ**

| パラメータ | 型 | 説明 |
|-----------|------|------|
| `status` | string | ステータスで絞り込み |
| `target_month` | string | 対象月（YYYY-MM）で絞り込み |
| `assignment_id` | UUID | 担当紐付けで絞り込み |
| `tutor_id` | UUID | 講師で絞り込み（admin_* のみ有効） |
| `parent_id` | UUID | 保護者で絞り込み（admin_* のみ有効） |

**GET /api/reports/export ／ /api/reports/export-daily クエリパラメータ**

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `target_month` | O | 対象月（YYYY-MM 形式） |
| `assignment_id` | X | 担当紐付け UUID（生徒別出力。未指定時は権限内の一括出力） |
| `format` | X | `/export` のみ。`pdf` 固定（それ以外は 422） |
| `scope` | X | `all` / `approved_only`。運営が指定すると最終承認済みのみに絞る（講師・保護者はもともと最終承認済みのみ） |
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
| POST | `/api/reports/{id}/parent-approve` | parent | 保護者承認（承認後そのまま運営へ自動提出） |
| POST | `/api/reports/{id}/parent-return` | parent | 保護者差戻し（comment 必須） |
| POST | `/api/reports/{id}/submit-to-admin` | tutor | 運営へ提出 |
| POST | `/api/reports/{id}/receive` | admin_receiver | 受付 |
| POST | `/api/reports/{id}/return-from-receiver` | admin_receiver | 受付担当差戻し（comment 必須） |
| POST | `/api/reports/{id}/re-review` | admin_reviewer | 再鑑（＝最終承認） |
| POST | `/api/reports/{id}/return-from-reviewer` | admin_reviewer | 再鑑者差戻し（comment 必須） |

> 旧フローの `POST /api/reports/{id}/admin-approve`（管理者の最終承認）および `POST /api/reports/{id}/return-from-master`（管理者差戻し）は **廃止済み**。`backend/app/api/workflow.py` には実装されていない。最終承認は再鑑者の `re-review`、完了後の差戻しは再鑑者の `return-from-reviewer` で行う。

### ワークフロー API（一括操作）

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| POST | `/api/reports/submit-to-parent-bulk` | tutor | 一括保護者依頼 |
| POST | `/api/reports/parent-approve-bulk` | parent | 一括保護者承認（承認後そのまま運営へ自動提出） |
| POST | `/api/reports/parent-return-bulk` | parent | 一括保護者差戻し |
| POST | `/api/reports/submit-to-admin-bulk` | tutor | 一括運営提出 |
| POST | `/api/reports/admin-receive-bulk` | admin_receiver | 一括受付 |
| POST | `/api/reports/admin-review-bulk` | admin_reviewer | 一括再鑑（＝最終承認） |
| POST | `/api/reports/admin-return-bulk` | admin_receiver / admin_reviewer | 一括差戻し（from_role 指定必須） |

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

※ `from_role` は `receiver` / `reviewer` のいずれか（旧 `master` は廃止）。一括受付は admin_receiver、一括再鑑（最終承認）は admin_reviewer のみが実行でき、admin_master / admin_chief では 403 となる。

### 未処理報告クローズ API

| メソッド | URL | 認可 | 概要 |
|---------|-----|------|------|
| GET | `/api/stale-count` | ログイン済み | ロール別未処理件数取得 |
| GET | `/api/stale-reports` | admin_receiver, admin_reviewer, admin_master, admin_chief | 未処理報告書一覧（先月以前 + 終端以外） |
| POST | `/api/reports/{report_id}/close` | admin_receiver, admin_reviewer, admin_master, admin_chief | 報告書をクローズ（close_reason 必須） |

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

本システムは新システム（業務連絡表）と同一 PostgreSQL（`tutor`）を共有し、`users` / `assignments` / `invitations` 等のテーブルを両システムで共用する。**全テーブルのカラム定義・制約・リレーション図は `../DATA_MODEL.md` に集約**しているため、本書では本システム（既存システム）固有の利用上の注意のみを記す。

### 本システムが主に利用するテーブル

| テーブル | 役割（既存システム視点） |
|---------|------------------------|
| `users` | 6ロール（tutor / parent / admin_receiver / admin_reviewer / admin_master / admin_chief）のアカウント。保護者の `skip_parent_approval` で保護者承認スキップを制御 |
| `assignments` | 講師×保護者×生徒の担当紐付け。リマインダー設定（`reminder_enabled` / `reminder_days_after` / `reminder_count`）を保持。`assignments.skip_parent_approval` は**未使用**（スキップ判定は `users.skip_parent_approval` に移設済み）。`system_type` は本システムでは `legacy` |
| `lesson_reports` | 報告書本体。`target_month`（YYYY-MM）、`status`（§5）、各承認タイムスタンプ列。`re_reviewed_at` は旧・最終承認待ち列だが、再鑑承認時には `admin_approved_at` も同時に記録される |
| `report_events` | 全ステータス遷移の監査ログ |
| `invitations` | 72時間有効のサインアップトークン（tutor は tutor_no を事前採番） |
| `password_reset_tokens` | 1時間有効のパスワードリセットトークン |
| `chat_messages` / `chat_reads` | 報告書単位のチャットと既読管理 |
| `notifications` | メール通知ログ |
| `mail_outbox` | 送信キュー（後述§9・`../INFRASTRUCTURE.md`）。1通ずつ間隔送信するための投函テーブル ※migration 0016 |

### 既存システム固有の補足

- **`report_events.action` 値**: `create`, `update`, `submit_to_parent`, `parent_approve`, `parent_return`, `parent_return_cancel`, `submit_to_admin`, `receive`, `return_from_receiver`, `re_review`, `return_from_reviewer`, `admin_approve`, `return_from_master`。このうち `admin_approve` / `return_from_master` は**廃止済みアクション**で、過去の `report_events` 履歴の表示のためだけに enum 値として残している（新規には記録されない）。
- 再鑑承認（`re_review`）時には、最終承認日時として `admin_approved_at` も同時に記録され、`status` は `admin_approved` になる。

> 共有テーブルが新システムでどう再利用されるか（例: `assignments.parent_id` が学校、`student_name` が学校名になる等）は `../DATA_MODEL.md` を参照。

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
| admin_chief | 未処理一覧閲覧・クローズ |
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
| `return_from_reviewer` | 【指導実績】運営から差戻しがありました | admin_receiver 全員（受付へ差戻し） | `notify_returned.txt` |
| `re_review`（＝最終承認） | 【指導実績】最終承認が完了しました | 講師・保護者（両方） | `notify_admin_approved.txt` |
| 受付による報告書修正 | 【指導実績】報告書が修正されました | 講師・保護者（承認スキップ時は保護者除く） | `notify_report_modified.txt` |
| 差戻し中報告書の講師修正 | 【指導実績】差戻し中の報告書が講師により修正されました | 直近の差戻し操作者 | `notify_tutor_edited.txt` |
| 保護者招待作成 | 【指導実績報告システム】保護者アカウントのご案内 | 招待先メールアドレス | `email/invitation.txt` |
| 講師招待作成 | 【指導実績報告システム】講師アカウントのご案内 | 招待先メールアドレス | `email/invitation_tutor.txt` |
| 運営スタッフ招待作成 | 【指導実績報告システム】スタッフアカウントのご案内 | 招待先メールアドレス | `email/invitation_staff.txt` |
| パスワードリセット依頼 | 【指導実績報告システム】パスワードリセットのご案内 | 対象ユーザー | `email/password_reset.txt` |

> **最終承認の通知は `re_review`（再鑑承認）時に送信される**。旧フローにあった `admin_approve` / `return_from_master` のトリガーは廃止されている。完了後に再鑑者が `return_from_reviewer` で差し戻した場合は、受付担当（admin_receiver 全員）宛てに差戻し通知が送られる。
>
> 件名・テンプレート内の `【指導実績報告システム】` 等のブランド表記はメールテンプレート（`backend/app/templates/email/`）に残る旧称であり、コード変更を要する。本書では実装文言をそのまま記載している（製品名リネームの反映はテンプレート側の別タスク）。

`transition()` では上記メールとは別に、状態変更時の監査・将来表示用として `notifications` テーブルへ `status_changed` レコードを作成する。

### メール送信経路（送信キュー）

本番では SMTP 直送ではなく**送信キュー（outbox）方式**を採用している。詳細・運用切替（`mailmode.sh`）は `../INFRASTRUCTURE.md` を参照。要点のみ:

- `MAIL_BACKEND` 環境変数で送信方式を切替（既定 `console`。自動テスト時は実送信ゼロ）
- `mail_outbox` テーブルへ投函し、`MAIL_SEND_INTERVAL_SECONDS`（既定4秒）間隔で1通ずつ送信
- 2システム横断でPGアドバイザリロックにより直列化（バースト対策）

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

### 様式（PDF 2種類）

| 様式 | エンドポイント | 用紙 | 内容 |
|------|--------------|------|------|
| 指導時間確認票（勤務表） | `GET /api/reports/export` | A4横・生徒×月ごとに1ページ | 講師名/講師No./合計時間数のヘッダー、明細（回数・指導日・曜日・在室した時間帯・指導時間数、最大20回）、月計・会員番号・生徒名・保護者名、受付/再鑑の電子承認印 |
| 指導日報 | `GET /api/reports/export-daily` | A4縦・1ページ5日分 | 紙の指導日報様式（`docs/イスト勤怠レポート for 代々木進学会/原本_日報.pdf`）を再現。ヘッダーに会員名・生徒名・会員No.・学年（小・中・高の丸囲み＋学年数）・講師名・講師No.、日ごとの枠に指導日（月日・曜日）・在室した時間帯（休憩の時間）・教科・ⓐ使用教材/テキスト名・ⓑ何を指導したか/単元など・ⓒ学習状況/問題点と対策・ⓓ宿題状況（A/B/C の丸囲み）・次回までの宿題・次回の予定（指導日/指導開始時刻）・会員認め印 |

### ファイル名

```
指導実績_{生徒名}_{YYYY年MM月}.pdf     … 指導時間確認票（一括出力時は 全生徒/全体 表記）
指導日報_{YYYY年MM月}.pdf              … 指導日報（仕様により固定名）
```

### 指導日報の会員認め印（電子印）

- 保護者（会員）承認を通過し、その承認が有効な報告書（`parent_approved` / `submitted_to_admin` / `received` / `re_reviewed` / `returned_to_receiver` / `admin_approved`）の枠に、承認日（JST）・「会員」・保護者名入りの朱色二重丸印を描画する。
- 差戻し中（`returned_to_tutor`）・クローズ済み（`closed`）・未承認の枠には押印しない（欄は空欄のまま）。
- 保護者承認スキップ設定の家庭では保護者承認が発生しないため、認め印欄は常に空欄となる。

### 指導日報のヘッダー学年

学年は指導報告に日ごとに記録されるため、ヘッダーには「月内で最後に学年（区分＋学年数）が記入された指導日」の値を表示する。

### 月の指導日数とページ

指導日報は指導日順に1ページ5日分で改ページする（例: 7日分 → 2ページ、2ページ目の残り3枠は未記入の様式のまま出力）。複数生徒の一括出力では生徒（担当）ごとに改ページする。

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

### 環境変数・Docker 構成・インフラ

本システムの環境変数（`DATABASE_URL` / `JWT_SECRET` / `SMTP_*` / `BASE_URL` / `MAIL_BACKEND` / `MAIL_SEND_INTERVAL_SECONDS` / `REMINDER_DAYS_BEFORE_MONTH_END` / `TIMEZONE` 等）、Docker Compose のサービス構成、AWS Lightsail 等の本番インフラ、SSH 接続・サーバー更新手順は **`../INFRASTRUCTURE.md` に集約**している。本システム固有の要点のみ:

- 本システムの公開 URL は `BASE_URL`（既定 `http://localhost:8000`）。新システムは `NEW_BASE_URL` を使う（同一 `.env` 共有のため別名）。招待・通知メールの URL 生成に使用
- 開発用アクセス URL: アプリ `http://localhost:8000`、Swagger `http://localhost:8000/docs`、ReDoc `http://localhost:8000/redoc`、MailHog Web UI `http://localhost:8025`
- `AUTO_CREATE_TABLES` は通常 `false`（スキーマは Alembic で管理）

---

## 12. 運用手順

開発・運用コマンドは Docker 上で実行する。日常的な起動/停止/ログ/マイグレーション/シードの操作手順は `OPERATION_MANUAL.md`（本システムの操作手順書）および `../INFRASTRUCTURE.md` を参照。ここでは開発者向けの要点のみを記す。

### 初回起動

```bash
git clone https://github.com/s-ohashi2/tutor-report-system.git
cd tutor-report-system
cp .env.example .env          # .env の JWT_SECRET を必ず変更すること
docker compose up -d --build
# http://localhost:8000 でログイン画面が出れば成功
```

### 通常の起動・停止

```bash
docker compose up -d          # 起動
docker compose down           # 停止（データは保持）
docker compose logs backend -f  # ログ確認
docker compose down -v        # 完全削除（データも消える）
```

### 依存パッケージ追加後の再ビルド

```bash
docker compose down && docker compose up -d --build   # pyproject.toml 変更後は --build 必須
```

### 開発用リセット

```bash
docker compose exec backend python app/scripts/dev_reset.py
```

### マイグレーション

```bash
docker compose exec backend alembic current      # 現在の状態
docker compose exec backend alembic upgrade head # 最新へ適用
docker compose exec backend alembic downgrade -1 # 1つ前へ
```

### バックアップ（暫定）

定期バックアップは未実装。手動手順:

```bash
docker compose exec db pg_dump -U postgres tutor > backup_$(date +%Y%m%d_%H%M%S).sql
docker compose exec -T db psql -U postgres tutor < backup_YYYYMMDD_HHMMSS.sql
```

> 本番反映・SMTP 切替（`mailmode.sh`）・サーバー更新の運用手順は `../INFRASTRUCTURE.md` / `../HANDOFF.md` に集約。

---

## 13. 将来拡張予定

### 本番メール送信（実装済み）

本番 SMTP の送信経路は整備済み。外部 SMTP（Gmail）を `.env` 差し替えで運用し、**送信キュー（`mail_outbox` + 送信間隔制御）も実装済み**（`MAIL_BACKEND` / `mailmode.sh` による切替、1通ずつ `MAIL_SEND_INTERVAL_SECONDS` 間隔送信、PGアドバイザリロックで2システム横断直列化）。AWS SES への移行は中止し、送信元は運用アドレスに統一済み。詳細は `../INFRASTRUCTURE.md` / `../HANDOFF.md` を参照。

### LINE 通知連携

- 現在はメール通知のみ実装済み
- LINE Messaging API を使ったプッシュ通知を追加予定
- `notifications.channel` カラムが `email` / `line` の切り替えを想定した設計になっている

### SSO 認証

- 現在は独自 JWT 認証（メール＋パスワード）
- Google Workspace / Microsoft Entra ID との SSO 連携を検討
- FastAPI の OAuth2 機構を活用して拡張予定

### AWS 移管

- 現在は Docker Compose によるオンプレミス（Lightsail）構成
- 想定移行先: Amazon ECS（Fargate）+ RDS PostgreSQL + CloudFront 等
- `DATABASE_URL` / `SMTP_*` 環境変数の変更で対応可能な設計

### 外部マスタ連携

- 現在はユーザー・担当紐付けを画面・CSV から管理
- 人事システム・塾管理システムとの CSV / API 連携による自動同期を検討
