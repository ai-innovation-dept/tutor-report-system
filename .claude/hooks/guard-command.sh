#!/usr/bin/env bash
# PreToolUse ガード（改修依頼 標準ルールの機械的ゲート / 導入: 管理番号 202607221651）。
#
# Bash・PowerShell の実行「前」に stdin の hook JSON を受け取り、次の場合だけ deny する:
#   (A) 本番メール事故防止 … 実メール送信につながるコマンド
#       - MAIL_BACKEND=smtp（送信バックエンドを実SMTPへ切替）
#       - python から drain_outbox / _send_via_smtp を呼ぶ（キューの実送信・直接送信）
#   (B) push前テストゲート … git push なのに直近120分以内の全テスト合格マーカー
#       (.claude/.tests-passed) が無い／古い
#
# この環境には jq が無いため、危険トークンが JSON 内に素の文字列で現れることを利用して
# 生の stdin を grep で判定する（python/jq 非依存＝高速・堅牢）。
# allow のときは何も出力せず exit 0（＝通常の許可フローへフォールバック）。
set -u

INPUT="$(cat)"
low="$(printf '%s' "$INPUT" | tr 'A-Z' 'a-z')"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
MARKER="$PROJECT_DIR/.claude/.tests-passed"

deny() {
  # 理由文はダブルクォート/改行を含めない（JSON を素の printf で組み立てるため）
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
  exit 0
}

# (A) 本番メール事故防止 ------------------------------------------------------
if printf '%s' "$low" | grep -qE 'mail_backend=[^a-z0-9]*smtp'; then
  deny "メール事故防止: MAIL_BACKEND=smtp（実SMTP切替）をブロックしました。検証は MAIL_BACKEND=console の pytest で行い、送信キュー(mail_outbox/work_mail_outbox)の行で確認してください。意図的に送るときだけ .claude/settings.json のフックを外してください。"
fi
if printf '%s' "$low" | grep -q 'python' && printf '%s' "$low" | grep -qE 'drain_outbox|_send_via_smtp'; then
  deny "メール事故防止: 実送信関数(drain_outbox/_send_via_smtp)の呼び出しをブロックしました。dev の .env は実SMTPのため実メールが飛びます。検証は console バックエンドの pytest で行ってください。"
fi

# (B) push前テストゲート ------------------------------------------------------
# 「git push」がコマンド境界（文字列先頭・JSONの " ・区切り ; & | ( ）の直後にある場合だけ
# 実際の push とみなす。コミットメッセージや echo/grep 内で語句として現れる「git push」は
# 誤検知しない（例: git commit -m "...git push ゲート..." は許可）。
if printf '%s' "$low" | grep -qE '(^|["|;&(])[[:space:]]*git[[:space:]]+push'; then
  if [ -z "$(find "$MARKER" -mmin -120 2>/dev/null)" ]; then
    deny "push前テスト未確認: 直近120分以内の全テスト合格マーカー(.claude/.tests-passed)がありません。両システムのフル pytest を通してから push してください（bash .claude/hooks/verify.sh で実行＋マーカー更新）。ドキュメントのみ等でテスト不要なら touch .claude/.tests-passed で更新できます。"
  fi
fi

exit 0
