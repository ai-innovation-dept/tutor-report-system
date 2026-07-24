# インフラ構成情報（本番）

> 本番 **AWS EC2** ホストで2システムを同一 `docker compose` で稼働: **イスト勤怠レポート for 代々木進学会**（旧称: 指導実績報告システム / `backend` / 8000）と **イスト勤怠レポート for EMPS**（旧称: 業務連絡表システム / `new_backend` / 8001）。ドキュメント索引は `README.md`。
> 最終更新: 2026-07-24（**AWS Lightsail → EC2 移行完了**。管理番号 202607241603。6項目の実態を移行担当より確定・反映済）。
> ⚠ **DNS切替・HTTPS化は未完**（現状は EC2 の IP:ポートに HTTP 直アクセス。下記「本番化前に必要な作業」参照）。
> ⚠ **二重稼働中**: 旧 Lightsail（52.197.43.164）が**現行として稼働継続**、EC2（52.199.22.60）は**本番切替前の検証**。両者はDBが別々のため、**切替直前に最終データ同期（`pg_dump`→`psql`）が必須**。
> 📄 詳細資料: 仕様=`docs/EC2/インフラ仕様書.md` / デプロイ=`docs/EC2/デプロイ手順書.md` / Lightsail差分=`docs/EC2/Lightsailとの差分.md`（管理番号 202607241629）。

## AWS EC2 インスタンス

| 項目 | 内容 |
|---|---|
| インスタンスID | `i-0ce3a2e284f376401` |
| リージョン | 東京（ap-northeast-1） |
| Elastic IP | **52.199.22.60** |
| OS | **Ubuntu 24.04 LTS**（旧Lightsailは 22.04。コンテナ構成のため差異は無影響） |
| インスタンスタイプ | **t3.small**（2vCPU / 2GB RAM）＋ **swap 4GB** |
| ストレージ | gp3 30GB |
| IAMロール | `tutor-ec2-s3-backup-role`（S3バックアップ用にアタッチ） |
| 稼働方式 | 単一 `docker compose`（db / mailhog / backend:8000 / new_backend:8001） |

## バックアップ（S3）

| 項目 | 内容 |
|---|---|
| S3バケット | `tutor-report-system-backup-nxtech2026`（**準備済**） |
| IAMロール | `tutor-ec2-s3-backup-role`（EC2にアタッチ＝アクセスキー不要でS3書込可・**準備済**） |
| 日次自動バックアップ（cron） | ⚠ **未実装（次タスク）**。バケット・IAM権限は準備済だが、`pg_dump` → `aws s3 cp` を回す cron と世代管理はまだ設定していない |

## ネットワーク

| 項目 | 内容 |
|---|---|
| Elastic IP | 52.199.22.60 |
| SSH | ポート22（`ubuntu` ＋ キーペア `tutor-ec2-key`） |
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

- ユーザー名 `ubuntu` ／ キーペア名 `tutor-ec2-key`（秘密鍵 `tutor-ec2-key.pem`）。**PCから鍵SSH**（旧 Lightsail のブラウザ SSH は使えない）。
- 接続例: `ssh -i tutor-ec2-key.pem ubuntu@52.199.22.60`
- 秘密鍵 `.pem` は**作業者PCローカルに保管**（例: `C:\Users\<user>\KintaiApp\`）。リポジトリ・チャット等には置かない。

## 現在の状態

| 項目 | 状態 |
|---|---|
| ホスティング | ✅ AWS EC2（移行完了・両アプリ稼働確認済） |
| ドメイン | ⚠ 未切替（DNSは旧 `163.44.176.16` のまま。目標: `kintai-yoyogi`/`kintai-emps.haken.net` → 52.199.22.60） |
| HTTPS | ⚠ 未対応（IP:ポートのHTTP＝平文） |
| メール送信 | ✅ `MAIL_BACKEND=console`（**送信オフ＝実メールは飛ばない**）。`BASE_URL=http://52.199.22.60:8000` / `NEW_BASE_URL=http://52.199.22.60:8001`（リンクは新IPを指すが送信オフのため実送信なし） |
| DB | ✅ コンテナ内PostgreSQL（`postgres:16-alpine`）維持。RDS未移行（`DATABASE_URL` 一本で将来RDS化しやすい設計は維持） |
| バックアップ | ⚠ バケット・IAMロールは準備済／**日次cron自動化は未実装**（次タスク） |

## 本番化前に必要な作業（EC2移行後の残タスク）

- [ ] **DNS切替**: `kintai-yoyogi.haken.net` / `kintai-emps.haken.net` を **52.199.22.60** へ向ける（`*.haken.net` ワイルドカードを個別Aレコードで上書き）
- [ ] **nginx リバースプロキシ**（Host振り分け: `kintai-yoyogi`→127.0.0.1:8000 / `kintai-emps`→127.0.0.1:8001）
- [ ] **HTTPS化**（Let's Encrypt。2ドメイン個別 or `*.haken.net` ワイルドカードは DNS-01）
- [ ] `.env` の `BASE_URL` / `NEW_BASE_URL` を新HTTPS URLへ更新（**メールリンク直結**。現在は IP:ポートのHTTP）
- [ ] HTTPS化後、**8000/8001 の外部公開を閉じる**（セキュリティグループを 22/80/443 のみに）
- [ ] **日次自動バックアップ（cron）の実装**（S3バケット `tutor-report-system-backup-nxtech2026`・IAMロールは準備済。`pg_dump`→`aws s3 cp` の cron ＋世代管理が未実装＝次タスク）
- [ ] 監視アラート設定
- [ ] 実メール配信の有効化（`bash mailmode.sh live` ＋ `.env` `MAIL_LIVE_*`）※現状は `MAIL_BACKEND=console`＝送信オフ。**DNS/HTTPS/BASE_URL更新後に有効化する**のが安全

## サーバー更新手順

コードを更新した後にサーバーへ反映する手順（配置ディレクトリ `~/tutor-report-system`＝`/home/ubuntu/tutor-report-system`）：

### 1. ローカルでコードを修正してGitHubにプッシュ

```bash
git add .
git commit -m "修正内容"
git push
```

### 2. EC2 に SSH して以下を実行

```bash
ssh -i tutor-ec2-key.pem ubuntu@52.199.22.60
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
