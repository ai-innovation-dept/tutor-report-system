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

### 開発用データリセット（開発環境のみ）

```bash
sudo docker compose exec backend python -m app.scripts.dev_reset
```
