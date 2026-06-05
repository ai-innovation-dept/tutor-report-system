# Data Model（データモデル）

本書は、本リポジトリで稼働する **2つのシステム** のデータモデルを記載する。

| システム | 用途 | ポート | バックエンド | Alembic バージョン管理 |
|----------|------|:------:|--------------|------------------------|
| **旧システム（legacy）** | 家庭教師 指導実績報告（tutor→parent→admin の承認フロー） | 8000 | `backend/` | `alembic_version` |
| **新システム（new / work）** | 業務連絡表（tutor→school→office→sales→経理 の承認フロー） | 8001 | `new_backend/` | `work_alembic_version` |

両システムは **同一の PostgreSQL データベース（`tutor`）** を共有する。`users` / `assignments` / `invitations` / `password_reset_tokens` テーブルは両システムが読み書きする共有テーブルで、**スキーマ（テーブル定義）は旧システムの Alembic が管理する**。新システムは `work_` プレフィックスの専用テーブル群を追加で持ち、これらは新システムの Alembic（`new_backend/migrations/`）が管理する。

最終更新: 2026-06-05

---

## 1. 共有テーブル（両システム）

`users` / `assignments` / `invitations` / `password_reset_tokens` は両システムで共有する。新システム導入にあたり、いくつかのカラムが追加されている（下表の「追加元」列を参照）。

### users

| カラム | 型 | 制約 | 説明 | 追加元 |
|--------|------|------|------|:------:|
| id | UUID | PK, default=uuid4 | ユーザーID | legacy |
| email | VARCHAR(255) | UNIQUE, INDEX, NOT NULL | メールアドレス（ログインID） | legacy |
| password_hash | VARCHAR(255) | NOT NULL | bcrypt ハッシュ | legacy |
| role | VARCHAR(32) | INDEX, NOT NULL | 主ロール（後述のロール一覧） | legacy |
| roles | JSON | NULL | 複数ロール保有時のロール配列 | legacy |
| display_name | VARCHAR(100) | NOT NULL | 表示名（氏名／学校名） | legacy |
| tutor_no | VARCHAR(20) | NULL | 講師番号（旧システムの採番） | legacy |
| phone | VARCHAR(20) | NULL | 電話番号 | legacy |
| is_active | BOOLEAN | NOT NULL, default=True | 有効フラグ | legacy |
| deleted_at | TIMESTAMP WITH TZ | NULL | 論理削除日時（ソフトデリート） | legacy |
| user_no | VARCHAR(20) | NULL | 新システムのユーザー番号（T/S/X 番号帯） | **new** |
| allowed_systems | JSON | NULL | アクセス可能システムの配列 | **new** |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 | legacy |
| updated_at | TIMESTAMP WITH TZ | NOT NULL | 更新日時 | legacy |

> ロールは旧システム・新システムで値域が異なる。同一ユーザーが両システムにまたがって複数ロールを持つ場合は `roles` 配列で表現する。詳細は「§4 ロール一覧」を参照。

### assignments（担当紐付け）

旧システムでは「講師×保護者×生徒」、新システムでは「講師×学校」の紐付けとして用いる（新システムでは `parent_id` に学校ユーザーを格納し、`student_name` には学校名を入れる運用）。

| カラム | 型 | 制約 | 説明 | 追加元 |
|--------|------|------|------|:------:|
| id | UUID | PK, default=uuid4 | 紐付けID | legacy |
| tutor_id | UUID | FK(users.id), INDEX, NOT NULL | 講師ID | legacy |
| parent_id | UUID | FK(users.id), INDEX, NULL | 保護者ID（新: 学校ID） | legacy |
| student_name | VARCHAR(100) | NOT NULL | 生徒名（新: 学校名） | legacy |
| is_active | BOOLEAN | NOT NULL, default=True | 有効フラグ | legacy |
| skip_parent_approval | BOOLEAN | NOT NULL, default=False | 保護者／学校承認スキップ | legacy |
| reminder_enabled | BOOLEAN | NOT NULL, default=False | リマインダー有効 | legacy |
| reminder_days_after | INTEGER | NOT NULL, default=1 | リマインダー間隔（日） | legacy |
| reminder_count | INTEGER | NOT NULL, default=1 | リマインダー最大回数 | legacy |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 | legacy |
| system_type | VARCHAR(10) | NULL, default='legacy' | 所属システム（'legacy' / 'work'） | **new** |

> 新システムでは `skip_parent_approval` を「学校承認スキップ」フラグとして転用する。

### invitations（招待）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 招待ID |
| email | VARCHAR(255) | INDEX, NOT NULL | 招待先メール |
| role | VARCHAR(32) | NOT NULL | 付与ロール |
| display_name | VARCHAR(100) | NULL | 表示名 |
| tutor_no | VARCHAR(20) | NULL | 講師番号。新システムでは `user_no`（T/S/X 番号）を格納 |
| assignment_id | UUID | FK(assignments.id), NULL | 紐付けID |
| token | VARCHAR(128) | UNIQUE, INDEX, NOT NULL | 招待トークン |
| invited_by | UUID | FK(users.id), NULL | 招待者 |
| expires_at | TIMESTAMP WITH TZ | NOT NULL | 有効期限（72時間） |
| accepted_at | TIMESTAMP WITH TZ | NULL | 受諾日時 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |

### password_reset_tokens

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | ID |
| user_id | UUID | FK(users.id), INDEX, NOT NULL | 対象ユーザー |
| token | VARCHAR(128) | UNIQUE, INDEX, NOT NULL | リセットトークン |
| expires_at | TIMESTAMP WITH TZ | NOT NULL | 有効期限 |
| used_at | TIMESTAMP WITH TZ | NULL | 使用日時 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |

---

## 2. 旧システム専用テーブル（legacy / port 8000）

### ER 図（旧システム）

```mermaid
erDiagram
  users ||--o{ assignments : "tutor_id / parent_id"
  assignments ||--o{ lesson_reports : "assignment_id"
  users ||--o{ lesson_reports : "tutor_id / parent_id / closed_by"
  lesson_reports ||--o{ report_events : "report_id"
  lesson_reports ||--o{ chat_messages : "report_id"
  chat_messages ||--o{ chat_reads : "message_id"
  users ||--o{ notifications : "user_id"
  assignments ||--o{ invitations : "assignment_id"
  users ||--o{ password_reset_tokens : "user_id"
```

### ReportStatus 列挙値（旧システム）

| 値 | 日本語名 | 終端 |
|----|---------|:----:|
| `draft` | 下書き | |
| `awaiting_parent_approval` | 保護者承認待ち | |
| `parent_approved` | 保護者承認済み | |
| `submitted_to_admin` | 運営提出済み | |
| `received` | 受付済み | |
| `re_reviewed` | 再鑑済み | |
| `admin_approved` | 最終承認済み | ✓ |
| `returned_to_tutor` | 講師へ差戻し | |
| `returned_to_receiver` | 受付へ差戻し | |
| `closed` | クローズ | ✓ |

Enum は Python 側（`backend/app/models/entities.py`）で定義し、DB には `VARCHAR(32)` として保存する。

### ReportAction 列挙値（report_events.action）

`create`, `update`, `submit_to_parent`, `parent_approve`, `parent_return`, `parent_return_cancel`, `submit_to_admin`, `receive`, `return_from_receiver`, `re_review`, `return_from_reviewer`, `admin_approve`, `return_from_master`

> クローズ操作（`close`）は `ReportAction` enum には含まれず、未処理報告クローズ機能（`backend/app/api/stale.py`）で直接処理・記録される。

### lesson_reports（報告書）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK | 報告書ID |
| assignment_id | UUID | FK(assignments.id), INDEX | 担当紐付けID |
| tutor_id | UUID | FK(users.id), INDEX | 講師ID |
| parent_id | UUID | FK(users.id), INDEX, NULL | 保護者ID |
| lesson_date | DATE | NOT NULL | 指導日 |
| start_time | TIME | NOT NULL | 開始時刻 |
| end_time | TIME | NOT NULL | 終了時刻 |
| break_minutes | INTEGER | NOT NULL, default=0 | 休憩時間（分） |
| subject | VARCHAR(100) | NULL | 科目 |
| content | TEXT | NOT NULL | 指導内容 |
| status | VARCHAR(32) | INDEX, NOT NULL | ReportStatus 値 |
| target_month | VARCHAR(7) | INDEX, NOT NULL | 対象月（YYYY-MM） |
| submitted_to_parent_at | TIMESTAMP WITH TZ | NULL | 保護者送信日時 |
| parent_approved_at | TIMESTAMP WITH TZ | NULL | 保護者承認日時 |
| submitted_to_admin_at | TIMESTAMP WITH TZ | NULL | 運営提出日時 |
| received_at | TIMESTAMP WITH TZ | NULL | 受付日時 |
| re_reviewed_at | TIMESTAMP WITH TZ | NULL | 再鑑日時 |
| admin_approved_at | TIMESTAMP WITH TZ | NULL | 最終承認日時 |
| stale_since | TIMESTAMP WITH TZ | NULL | 未処理判定日時（初回検出時刻。以降は上書きしない） |
| closed_at | TIMESTAMP WITH TZ | NULL | クローズ日時 |
| closed_by | UUID | FK(users.id), INDEX, NULL | クローズ実行者ID |
| close_reason | VARCHAR(500) | NULL | クローズ理由（クローズ時は必須） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |
| updated_at | TIMESTAMP WITH TZ | NOT NULL | 更新日時 |

### その他の旧システムテーブル

`report_events`（操作履歴・監査ログ）, `chat_messages`（報告書チャット）, `chat_reads`（既読管理）, `notifications`（メール送信ログ）。詳細スキーマは `SPECIFICATION.md §7` を参照。

---

## 3. 新システム専用テーブル（new / work / port 8001）

新システムのテーブルはすべて `work_` プレフィックスを持つ。定義は `new_backend/app/models/work.py`、マイグレーションは `new_backend/migrations/` で管理する。

### ER 図（新システム）

```mermaid
erDiagram
  users ||--o{ assignments : "tutor_id / parent_id(=school)"
  users ||--o{ work_assignment_profiles : "tutor_id / school_id"
  assignments ||--|| work_assignment_profiles : "assignment_id (1:1)"
  assignments ||--o{ work_reports : "assignment_id"
  users ||--o{ work_reports : "tutor_id / closed_by"
  work_reports ||--o{ work_report_events : "report_id"
  work_reports ||--o{ work_chat_messages : "report_id"
  work_chat_messages ||--o{ work_chat_reads : "message_id"
  users ||--o{ work_notifications : "user_id"
  work_reports ||--o{ work_notifications : "report_id"
```

### WorkStatus 列挙値（新システム）

| 値 | 日本語名 | 承認担当ロール | 終端 |
|----|---------|----------------|:----:|
| `draft` | 下書き | tutor | |
| `awaiting_school` | 学校承認待ち | school | |
| `awaiting_office` | 事務確認待ち | office | |
| `awaiting_sales` | 営業確認待ち | sales | |
| `awaiting_finance` | 経理（最終）確認待ち | admin_master | |
| `approved` | 最終承認済み（完了） | — | ✓ |
| `returned_to_tutor` | 講師へ差戻し | tutor | |
| `returned_to_office` | 事務へ差戻し | office | |
| `closed` | クローズ | — | ✓ |

ステータス遷移はすべて `new_backend/app/workflow/definitions.py` の `TRANSITIONS` テーブルが唯一の定義源（single source of truth）。

#### 状態遷移サマリー

```
draft ──submit(tutor)──▶ awaiting_school ──approve(school)──▶ awaiting_office
draft ──skip_school(sales/office/admin_master)──▶ awaiting_office
awaiting_office ──approve(office)──▶ awaiting_sales ──approve(sales)──▶ awaiting_finance
awaiting_finance ──approve(admin_master)──▶ approved（完了）

差戻し:
  awaiting_school ──return(school)──▶ returned_to_tutor
  awaiting_office ──return(office)──▶ returned_to_tutor
  awaiting_sales  ──return(sales)──▶ returned_to_office
  awaiting_finance──return(admin_master)──▶ returned_to_office
  approved        ──return(admin_master)──▶ returned_to_office（完了後の修正依頼）

再提出 / 事務の処理:
  returned_to_tutor ──submit(tutor)──▶ awaiting_school
  returned_to_office ──submit(office)──▶ awaiting_sales
  returned_to_office ──approve(office)──▶ awaiting_sales（事務が前進）
  returned_to_office ──return(office)──▶ returned_to_tutor（事務が講師へ差戻し）
```

> アクション（`WorkAction`）: `submit` / `approve` / `return` / `skip_school` / `close`。`return` はコメント必須。

### work_assignment_profiles（契約マスタ 兼 フォーム設定）

1契約 = (講師, 学校) ごとに1件で、1つの `assignment` に 1:1 対応する。経理の「契約管理」画面で登録し、講師の報告書フォーム（動的列定義）の元データとなる。

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 契約ID |
| assignment_id | UUID | FK(assignments.id), UNIQUE, INDEX | 紐付けID（1:1） |
| tutor_id | UUID | FK(users.id), INDEX, NOT NULL | 講師ID |
| school_id | UUID | FK(users.id), INDEX, NOT NULL | 学校ID |
| form_type | VARCHAR(50) | NOT NULL | フォーム種別（例: monthly_dispatch） |
| contract_meta | JSONB | default={} | 契約メタ（任意項目） |
| customer_id | VARCHAR(50) | NULL | お客様ID |
| our_staff | VARCHAR(100) | NULL | 弊社担当 |
| contract_start | DATE | NULL | 契約開始日 |
| contract_end | DATE | NULL | 契約終了日 |
| monthly_minutes | INTEGER | NULL | 月固定分数 |
| weekly_lessons | INTEGER | NULL | 週コマ数 |
| shift_note | TEXT | NULL | シフト・備考 |
| work_content | TEXT | NULL | 従事業務内容 |
| task_name_1..5 | VARCHAR(100) | NULL | 委託業務①〜⑤の業務名 |
| task_id_1..5 | VARCHAR(50) | NULL | 委託業務①〜⑤の委託業務ID |
| contract_id_1..5 | VARCHAR(50) | NULL | 委託業務①〜⑤の個別契約ID |
| scoring_enabled | BOOLEAN | NOT NULL, default=False | 採点欄を有効化 |
| scoring_task_id | VARCHAR(50) | NULL | 採点の委託業務ID |
| scoring_contract_id | VARCHAR(50) | NULL | 採点の個別契約ID |
| is_active | BOOLEAN | NOT NULL, default=True | 有効フラグ（論理削除に使用） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |
| updated_at | TIMESTAMP WITH TZ | NOT NULL | 更新日時 |

**制約**: `UNIQUE(tutor_id, school_id)`（講師×学校で1契約）。

> **委託業務と採点の報告書反映**: 委託業務①〜⑤は常に「分のみ」（「業務名（分）」列）。採点は `scoring_enabled=True` のときのみ報告書末尾に「採点（回）」列（1セル併記＝回数＋分数）を生成する。動的列定義は `services/contract_form_service.build_column_definition()` が生成する。

### work_reports（業務連絡表）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | 報告書ID |
| assignment_id | UUID | FK(assignments.id), INDEX | 紐付けID |
| tutor_id | UUID | FK(users.id), INDEX | 講師ID |
| target_month | VARCHAR(7) | INDEX, NOT NULL | 対象月（YYYY-MM） |
| form_type | VARCHAR(50) | NOT NULL | フォーム種別 |
| form_data | JSONB | default={} | 報告書本体（後述の構造） |
| status | VARCHAR(32) | INDEX, NOT NULL | WorkStatus 値 |
| current_approver_role | VARCHAR(32) | NULL | 現在の承認担当ロール |
| submitted_at | TIMESTAMP WITH TZ | NULL | 提出日時 |
| stale_since | TIMESTAMP WITH TZ | NULL | 未処理判定日時 |
| closed_at | TIMESTAMP WITH TZ | NULL | クローズ日時 |
| closed_by | UUID | FK(users.id), INDEX, NULL | クローズ実行者 |
| close_reason | VARCHAR(500) | NULL | クローズ理由 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |
| updated_at | TIMESTAMP WITH TZ | NOT NULL | 更新日時 |

**制約**: `UNIQUE(assignment_id, target_month)`（紐付け×月で1報告書）。

#### form_data（JSONB）の構造

```jsonc
{
  "lines": [                       // 明細行（最大行数はフォーム定義の max_lines）
    {
      "date": "2026-06-01",
      "start": "16:00",
      "end": "18:00",
      "subject_period": "3",       // 担当時限（1〜10）
      "task_minutes_1": "60",      // 委託業務①（分）
      "scoring_count": "5",        // 採点（回） ※scoring_enabled時
      "scoring_minutes": "30",     // 採点（分） ※scoring_enabled時
      "break_minutes": "0",
      "commute_fee": "0",
      "note": "..."
    }
  ],
  "meta": {                        // 業務連絡表ヘッダー情報
    "dispatch_place_school_id": "<UUID>",  // 派遣先（学校）= assignment.parent
    "dispatch_place_name": "○○学園",       // 学校名スナップショット
    "dispatch_place_address": "...",
    "tutor_no": "T1001",
    "customer_id": "...",          // 契約から初期反映
    "our_staff": "...",
    "contract_period": "...",
    "monthly_minutes_fixed": "...",
    "weekly_lessons": "...",
    "work_content": "...",
    "note_schedule": "...",
    "requests": "...",
    "column_definition": [ /* 作成時の動的列定義スナップショット */ ]
  }
}
```

> `meta.column_definition` は報告書作成時に契約から生成した列定義のスナップショット。これにより、保存済みの報告書は後からの契約変更の影響を受けない（新規作成時のみ契約値を初期反映する設計）。

### work_report_events（操作履歴・監査ログ）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | ID |
| report_id | UUID | FK(work_reports.id), INDEX | 報告書ID |
| actor_id | UUID | FK(users.id), INDEX | 実行者 |
| action | VARCHAR(32) | NOT NULL | submit / approve / return / skip_school / close |
| from_status | VARCHAR(32) | NULL | 遷移前ステータス |
| to_status | VARCHAR(32) | NULL | 遷移後ステータス |
| comment | TEXT | NULL | コメント（差戻し時は必須） |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 実行日時 |

### work_chat_messages / work_chat_reads（報告書チャット）

`work_chat_messages`: id（PK）, report_id（FK work_reports）, sender_id（FK users）, body（TEXT）, created_at。
`work_chat_reads`: message_id（FK work_chat_messages, PK）, user_id（FK users, PK）, read_at。`UNIQUE(message_id, user_id)`。

### work_notifications（通知ログ）

| カラム | 型 | 制約 | 説明 |
|--------|------|------|------|
| id | UUID | PK, default=uuid4 | ID |
| user_id | UUID | FK(users.id), INDEX | 宛先ユーザー |
| report_id | UUID | FK(work_reports.id), NULL, INDEX | 関連報告書 |
| channel | VARCHAR(16) | default='email' | チャネル |
| type | VARCHAR(32) | NOT NULL | 通知種別 |
| subject | VARCHAR(255) | NOT NULL | 件名 |
| body | TEXT | NOT NULL | 本文 |
| sent_at | TIMESTAMP WITH TZ | NULL | 送信日時 |
| read_at | TIMESTAMP WITH TZ | NULL | 既読日時 |
| created_at | TIMESTAMP WITH TZ | NOT NULL | 作成日時 |

---

## 4. ロール一覧（両システム）

ロールは `users.role`（主ロール）／ `users.roles`（複数ロール）に格納する。

| ロール値 | システム | 役割 |
|----------|:--------:|------|
| `tutor` | 共通 | 講師。報告書の作成・提出 |
| `parent` | 旧 | 保護者。子の報告書を承認／差戻し |
| `admin_receiver` | 旧 | 運営（受付）。提出報告の受付 |
| `admin_reviewer` | 旧 | 運営（再鑑）。受付済み報告の再鑑 |
| `admin_master` | 共通 | 最終承認・ユーザー管理。**新システムでは「経理」を兼ねる** |
| `school` | 新 | 学校担当。講師の業務連絡表を承認／差戻し |
| `office` | 新 | 事務。学校承認後の確認、差戻し対応 |
| `sales` | 新 | 営業。事務確認後の確認 |

> 新システムの最終承認（経理）は `admin_master` ロールが担当する（`awaiting_finance` → `approved`）。

---

## 5. Alembic マイグレーションの分離

- **旧システム**: `backend/alembic/versions/`（`0001`〜）。バージョンテーブルは `alembic_version`。
- **新システム**: `new_backend/migrations/`（`0001`〜）。バージョンテーブルは `work_alembic_version`。

共有テーブル（`users` / `assignments` 等）のスキーマは旧システム側が管理する。新システムは共有テーブルへの**カラム追加**（`users.user_no` / `users.allowed_systems` / `assignments.system_type`）と `work_` テーブル群の作成のみを行う。新システムの Docker コンテナは起動時に `alembic upgrade head` を実行する。
