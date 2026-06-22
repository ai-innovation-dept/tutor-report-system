# ドキュメント索引

本リポジトリは「**イスト勤怠レポート**」の **2システム構成**です。資料はシステム別フォルダ＋共通資料で分類しています。まずこの索引から辿ってください。

## システム対応表

| 区分 | 製品名（本ドキュメントでの呼称） | 旧称 | コード | ポート |
|---|---|---|---|---|
| 既存 | **イスト勤怠レポート for 代々木進学会** | 指導実績報告システム | `backend/` | 8000 |
| 新 | **イスト勤怠レポート for EMPS** | 業務連絡表システム | `new_backend/` | 8001 |

両システムは同一の PostgreSQL を共有し、`users` / `assignments` / `invitations` / `password_reset_tokens` テーブルを共用します（`assignments` は `system_type` 列で所属システムを区別）。

## システム別資料

### イスト勤怠レポート for 代々木進学会（既存）
- [`イスト勤怠レポート for 代々木進学会/SPECIFICATION.md`](イスト勤怠レポート%20for%20代々木進学会/SPECIFICATION.md) — 開発者向け仕様（概要・ロール・業務フロー・API・ステータス・通知・エクスポート 等）
- [`イスト勤怠レポート for 代々木進学会/OPERATION_MANUAL.md`](イスト勤怠レポート%20for%20代々木進学会/OPERATION_MANUAL.md) — 運用者向け操作手順

### イスト勤怠レポート for EMPS（新）
- [`イスト勤怠レポート for EMPS/SPECIFICATION.md`](イスト勤怠レポート%20for%20EMPS/SPECIFICATION.md) — 開発者向け仕様（契約管理・動的フォーム列・業務フロー・API 等）
- [`イスト勤怠レポート for EMPS/OPERATION_MANUAL.md`](イスト勤怠レポート%20for%20EMPS/OPERATION_MANUAL.md) — 運用者向け操作手順

## 共通資料（両システム）

- [`DATA_MODEL.md`](DATA_MODEL.md) — データモデル（共有テーブル＋各システム固有テーブル）。**スキーマの正本**。
- [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md) — 本番インフラ・デプロイ・クリーン投入手順
- [`HANDOFF.md`](HANDOFF.md) — 引継ぎ・未対応事項・本番反映状況
- [`クローズ機能ガイド.md`](クローズ機能ガイド.md) — クローズ／強制クローズの**具体例つきやさしい解説**（両システム）
- リポジトリ直下 [`../CLAUDE.md`](../CLAUDE.md)（開発ガイド／Claude Code 用）・[`../README.md`](../README.md)（クイックスタート）

> **注（次段で更新予定）**: 共通資料 `DATA_MODEL.md` / `INFRASTRUCTURE.md` / `HANDOFF.md` は、新名称への統一と内容の最新化（新システムの**経理ステップ廃止・事務事前確認の追加**などワークフロー修正、`work_assignment_profiles` のスキーマ差分、`mail_outbox`/`work_mail_outbox` 追加、SMTP送信キュー・`mailmode.sh`、ポート8001 など）を**次段で実施予定**です。ワークフロー／スキーマの確定情報は、各システムの `SPECIFICATION.md` とコード（`backend/app/...`・`new_backend/app/...`、特に `new_backend/app/workflow/definitions.py`）を正とします。

## アーカイブ

- [`OLD/`](OLD/) — 旧資料（`API.md` / `PHASE_PLAN.md` / `IMPLEMENTATION_LOG.md`）。各々の後継・アーカイブ理由は [`OLD/README.md`](OLD/README.md) を参照。

## 旧オンボーディング資料（据え置き・要更新）

- `build_manual.py` / `build_manual_pptx.py` と生成物 `操作手順書.pdf` / `操作手順書.pptx` … 旧「指導実績報告システム」単独・旧承認モデル前提の illustrated オンボーディング資料。現行仕様（再鑑＝最終承認、新システム）とは差異があるため**参考扱い**。今後も使う場合は再生成時に最新化が必要。

---
最終更新: 2026-06-22
