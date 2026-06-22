# API

| Method | URL | Auth | Summary |
|---|---|---|---|
| POST | `/api/auth/login` | public | JWT 発行 |
| GET | `/api/auth/me` | login | ログインユーザー |
| POST | `/api/users` | admin_master | ユーザー作成 |
| GET | `/api/users` | admin_* | ユーザー一覧 |
| PATCH | `/api/users/{id}` | admin_master | ユーザー更新 |
| POST | `/api/assignments` | admin_master | 講師・保護者・生徒紐付け |
| GET | `/api/assignments` | login | 権限範囲の紐付け一覧 |
| POST | `/api/reports` | tutor | 下書き作成 |
| GET | `/api/reports` | login | 権限範囲の報告書一覧 |
| PATCH | `/api/reports/{id}` | tutor | draft/returned の編集 |
| DELETE | `/api/reports/{id}` | tutor/admin_master | draft 削除 |
| POST | `/api/reports/{id}/submit-to-parent` | tutor | 保護者へ承認依頼 |
| POST | `/api/reports/{id}/parent-approve` | parent | 保護者承認 |
| POST | `/api/reports/{id}/parent-return` | parent | 保護者差戻 |
| POST | `/api/reports/{id}/submit-to-admin` | tutor | 運営へ提出 |
| POST | `/api/reports/submit-to-admin-bulk` | tutor | 一括提出 |
| POST | `/api/reports/{id}/receive` | admin_receiver | 受付 |
| POST | `/api/reports/{id}/re-review` | admin_reviewer | 再鑑 |
| POST | `/api/reports/{id}/admin-approve` | admin_master | 最終承認 |
| GET | `/api/reports/{id}/messages` | related users | チャット差分取得 |
| POST | `/api/reports/{id}/messages` | related users | チャット投稿 |

