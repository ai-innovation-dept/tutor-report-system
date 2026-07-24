# インフラ構成情報（本番）

> 本番 **AWS EC2** ホストで2システムを同一 `docker compose` で稼働: **イスト勤怠レポート for 代々木進学会**（旧称: 指導実績報告システム / `backend` / 8000）と **イスト勤怠レポート for EMPS**（旧称: 業務連絡表システム / `new_backend` / 8001）。ドキュメント索引は `README.md`。
> 最終更新: 2026-07-24（**AWS Lightsail → EC2 移行完了**。管理番号 202607241603 で実態反映）。
> ⚠ **DNS切替・HTTPS化は未完**（現状は EC2 の IP:ポートに HTTP 直アクセス。下記「本番化前に必要な作業」参照）。「要確認」の項目は移行担当からの情報待ち。

## AWS EC2 インスタンス

| 項目 | 内容 |
|---|---|
| インスタンスID | `i-0ce3a2e284f376401` |
| リージョン | 東京（ap-northeast-1） |
| Elastic IP | **52.199.22.60** |
| OS | 要確認（旧Lightsailは Ubuntu 22.04 LTS） |
| インスタンスタイプ | 要確認 |
| IAMロール | `tutor-ec2-s3-backup-role`（S3バックアップ用にアタッチ） |
| 稼働方式 | 単一 `docker compose`（db / mailhog / backend:8000 / new_backend:8001） |

## バックアップ（S3）

| 項目 | 内容 |
|---|---|
| S3バケット | `tutor-report-system-backup-nxtech2026` |
| IAMロール | `tutor-ec2-s3-backup-role`（EC2にアタッチ＝アクセスキー不要でS3書込可） |
| 取得内容・スケジュール | 要確認（想定: cron で `pg_dump` → `aws s3 cp` でバケットへ。実スクリプト・cron・世代管理を要確認） |

## ネットワーク

| 項目 | 内容 |
|---|---|
| Elastic IP | 52.199.22.60 |
| SSH | ポート22（接続方法・鍵は要確認） |
| アプリ（既存=代々木進学会） | ポート8000（**現在インターネット公開中**＝2026-07-24 実機確認） |
| アプリ（新=EMPS） | ポート8001（**現在インターネット公開中**） |
| HTTP/HTTPS (80/443) | HTTPS化後に使用（現状は未使用） |

## アクセスURL（現在＝2026-07-24 稼働確認済）

| 用途 | URL |
|---|---|
| アプリ（既存=代々木進学会） | http://52.199.22.60:8000 （応答確認: title「指導報告・指導時間確認票」） |
| アプリ（新=EMPS） | http://52.199.22.60:8001 （応答確認: title「業務連絡表システム」） |
| API仕様 | http://52.199.22.60:8000/docs ・ http://52.199.22.60:8001/docs |

> ⚠ **`kintai-yoyogi.haken.net` / `kintai-emps.haken.net` はまだ使えない**。DNSが旧ワイルドカード（`163.44.176.16`＝旧LiteSpeed の既定ページ）を指したままで、EIP `52.199.22.60` へ切替されていない（2026-07-24 実機確認）。切替・nginx・HTTPS化の手順は `docs/EC2移行_引継ぎ_202607241011.md` Step6–8。
> ⚠ **現状はHTTP（平文）**。IP:ポート直アクセスのためログイン情報・JWT Cookie が暗号化されずに流れる。HTTPS化を優先すること。

## SSH接続方法

- EC2 キーペア（`.pem`）で SSH（ユーザー名・鍵の所在は**要確認**。旧 Lightsail コンソールのブラウザ SSH は使えない）。
- 例（要確認）: `ssh -i <キー.pem> ubuntu@52.199.22.60`（Ubuntu AMI なら既定ユーザーは `ubuntu`）。

## 現在の状態

| 項目 | 状態 |
|---|---|
| ホスティング | ✅ AWS EC2（移行完了・両アプリ稼働確認済） |
| ドメイン | ⚠ 未切替（DNSは旧 `163.44.176.16` のまま。目標: `kintai-yoyogi`/`kintai-emps.haken.net` → 52.199.22.60） |
| HTTPS | ⚠ 未対応（IP:ポートのHTTP＝平文） |
| メール送信 | 要確認（`MAIL_BACKEND` が console＝送信オフ / smtp(live)＝実配信 のいずれか） |
| DB | 要確認（コンテナ内PostgreSQL維持 or RDSへ移行） |
| バックアップ | ✅ S3構成あり（バケット + IAMロール。詳細スケジュールは要確認） |

## 本番化前に必要な作業（EC2移行後の残タスク）

- [ ] **DNS切替**: `kintai-yoyogi.haken.net` / `kintai-emps.haken.net` を **52.199.22.60** へ向ける（`*.haken.net` ワイルドカードを個別Aレコードで上書き）
- [ ] **nginx リバースプロキシ**（Host振り分け: `kintai-yoyogi`→127.0.0.1:8000 / `kintai-emps`→127.0.0.1:8001）
- [ ] **HTTPS化**（Let's Encrypt。2ドメイン個別 or `*.haken.net` ワイルドカードは DNS-01）
- [ ] `.env` の `BASE_URL` / `NEW_BASE_URL` を新HTTPS URLへ更新（**メールリンク直結**）
- [ ] HTTPS化後、**8000/8001 の外部公開を閉じる**（セキュリティグループを 22/80/443 のみに）
- [x] 自動バックアップ（S3バケット `tutor-report-system-backup-nxtech2026` + IAMロール構成済／cron・世代管理は要確認）
- [ ] 監視アラート設定
- [ ] 実メール配信の有効化（`bash mailmode.sh live` ＋ `.env` `MAIL_LIVE_*`）※現状の `MAIL_BACKEND` 要確認

## サーバー更新手順

コードを更新した後にサーバーへ反映する手順（SSH接続方法・配置ディレクトリは**要確認**。従来は `~/tutor-report-system`）：

### 1. ローカルでコードを修正してGitHubにプッシュ

```bash
git add .
git commit -m "修正内容"
git push
```

### 2. EC2 に SSH して以下を実行

```bash
cd tutor-report-system        # 配置ディレクトリは要確認
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

> ⚠ **全データ（ユーザー・報告書・契約・招待・通知 等）を削除します。** 実ユーザー運用開始後は
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
