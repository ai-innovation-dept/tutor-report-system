# インフラ構成情報

## AWS Lightsail インスタンス

| 項目 | 内容 |
|---|---|
| インスタンス名 | tutor-report-system |
| リージョン | 東京（ap-northeast-1） |
| OS | Ubuntu 22.04 LTS |
| スペック | 2GB RAM / 2vCPU / 60GB SSD |
| 月額 | $12 USD |

## ネットワーク

| 項目 | 内容 |
|---|---|
| 静的IP | 52.197.43.164 |
| SSH | ポート22 |
| HTTP | ポート80 |
| アプリ | ポート8000 |
| MailHog | ポート8025 |

## アクセスURL

| 用途 | URL |
|---|---|
| アプリ | http://52.197.43.164:8000 |
| API仕様 | http://52.197.43.164:8000/docs |
| メール確認 | http://52.197.43.164:8025 |

## SSH接続方法

LightsailコンソールのブラウザSSHから接続：
https://lightsail.aws.amazon.com

または：
- SSHユーザー：ubuntu
- デフォルトSSHキーはLightsailコンソールからダウンロード

## 現在の状態

| 項目 | 状態 |
|---|---|
| ドメイン | 未取得（IPアドレス直接アクセス）|
| HTTPS | 未対応（HTTP通信）|
| メール送信 | MailHog（仮想実際には送信されない）|
| DB | コンテナ内PostgreSQL |

## 本番化前に必要な作業

- [ ] ドメイン取得
- [ ] SSL証明書取得HTTPS化（Let's Encrypt推奨）
- [ ] 実SMTPサーバーへの切替（AWS SES推奨）
- [ ] PostgreSQLをLightsailマネージドDBへ移行（推奨）
- [ ] 自動バックアップ設定
- [ ] 監視アラート設定

## サーバー更新手順

コードを更新した後にサーバーへ反映する手順：

### 1. ローカルでコードを修正してGitHubにプッシュ

```bash
git add .
git commit -m "修正内容"
git push
```

### 2. LightsailのSSHで以下を実行

```bash
cd tutor-report-system
git pull
sudo docker compose up -d --build
```

### 3. DBスキーマ変更がある場合

```bash
sudo docker compose exec backend alembic upgrade head
```

### 4. allowed_systems 分離リリースの初回デプロイ時のみ（1回限り）

ユーザー所属を `allowed_systems` で分離したリリースを本番へ反映する際は、デプロイ後に
**1回だけ**正規化スクリプトを実行する。これにより `allowed_systems` 未設定の既存ユーザーが
ログインできなくなる事態を防ぐ（NULL→["legacy"]、admin_master→両システムを保証、既存値は尊重）。
冪等なので複数回実行しても安全。

```bash
# 念のためバックアップ
sudo docker compose exec -T db pg_dump -U postgres -d tutor > backup_$(date +%Y%m%d_%H%M%S).sql
# 正規化（このリリースの初回のみ）
sudo docker compose exec backend python -m app.scripts.normalize_allowed_systems
```

### 5. 本番をクリーンにして検証用サンプルユーザーのみにする場合（初回セットアップ・1回限り・破壊的）

通常デプロイ（`up -d --build`）では実行されない**手動ステップ**。`up -d --build` はコード反映と
マイグレーションのみで、**ユーザー等のデータは消えない**。本番を空にしてサンプルユーザー
（実在Gmail＋プラスエイリアスの6件）だけにするには、デプロイ後に下記を**1回だけ**実行する。

> ⚠️ **全データ（ユーザー・報告書・契約・招待・通知 等）を削除します。** 実ユーザー運用開始後は
> 実行しないこと。両システムは DB を共有するため、`backend` で1回実行すれば両系がクリーンになる。

```bash
# 必ずバックアップ
sudo docker compose exec -T db pg_dump -U postgres -d tutor > backup_$(date +%Y%m%d_%H%M%S).sql
# 全消去＋サンプルユーザー6件を投入（--yes が無いと実行されず使い方だけ表示）
sudo docker compose exec backend python -m app.scripts.seed_production --yes
# 確認（6件・@gmail.com のみになっていること）
sudo docker compose exec -T db psql -U postgres -d tutor -c "SELECT user_no, email, role, roles FROM users ORDER BY user_no;"
```

`ENVIRONMENT=production` を本番 `.env` に設定しておくこと（マイグレーション0014が
`supervisor@example.com` を投入しないガードが有効になる）。

### 開発用データリセット（開発環境のみ）

```bash
sudo docker compose exec backend python -m app.scripts.dev_reset
```
