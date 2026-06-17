#!/usr/bin/env bash
# メール送信モードの簡易切替（アプリ本体は変更せず .env を切り替えて再起動するだけ）。
#
#   sandbox : 検証モード（Mailtrap サンドボックスに送信＝実ユーザーには届かず全て捕捉）
#   off     : 送信オフ（メールを一切送らない。ログのみ）
#   live    : 実配信モード（Brevo 等で実際に届く）
#
# 認証情報はこのスクリプトに直書きせず、.env の以下グループから読み込む（.env.example 参照）:
#   MAIL_SANDBOX_HOST / _PORT / _USERNAME / _PASSWORD / _TLS
#   MAIL_LIVE_HOST    / _PORT / _USERNAME / _PASSWORD / _TLS / _FROM
#
# 使い方:
#   sudo bash mailmode.sh sandbox
#   sudo bash mailmode.sh off
#   sudo bash mailmode.sh live
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

ENV_FILE=".env"
mode="${1:-}"

[ -f "$ENV_FILE" ] || { echo "エラー: $ENV_FILE が見つかりません" >&2; exit 1; }

getv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

setv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

apply_group() {  # $1 = SANDBOX | LIVE
  local p="MAIL_$1_" host user pass port tls from
  host="$(getv "${p}HOST")"; user="$(getv "${p}USERNAME")"; pass="$(getv "${p}PASSWORD")"
  if [ -z "$host" ] || [ -z "$user" ] || [ -z "$pass" ]; then
    echo "エラー: .env に ${p}HOST / ${p}USERNAME / ${p}PASSWORD が設定されていません。" >&2
    echo "       先に .env へ認証情報グループを追記してください（.env.example 参照）。" >&2
    exit 1
  fi
  setv SMTP_HOST "$host"
  setv SMTP_USERNAME "$user"
  setv SMTP_PASSWORD "$pass"
  port="$(getv "${p}PORT")"; setv SMTP_PORT "${port:-587}"
  tls="$(getv "${p}TLS")";   setv SMTP_TLS "${tls:-starttls}"
  from="$(getv "${p}FROM")"
  if [ -n "$from" ]; then setv SMTP_FROM "$from"; setv NEW_SMTP_FROM "$from"; fi
}

case "$mode" in
  sandbox) apply_group SANDBOX; setv MAIL_BACKEND smtp ;;
  live)    apply_group LIVE;    setv MAIL_BACKEND smtp ;;
  off)     setv MAIL_BACKEND console ;;
  *) echo "使い方: sudo bash mailmode.sh sandbox|off|live" >&2; exit 1 ;;
esac

echo "----- 現在のメール設定 (mode=${mode}) -----"
grep -E '^(MAIL_BACKEND|SMTP_HOST|SMTP_PORT|SMTP_TLS|SMTP_USERNAME|SMTP_FROM)=' "$ENV_FILE"
echo "（SMTP_PASSWORD は非表示）"

docker compose up -d --force-recreate
echo "✅ メール送信モードを '${mode}' に切り替えました"
