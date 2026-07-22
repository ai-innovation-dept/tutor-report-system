#!/usr/bin/env bash
# 両システムのフル pytest を MAIL_BACKEND=console（実送信ゼロ）で実行し、
# 両方 pass したときだけ push前ゲート用マーカー .claude/.tests-passed を更新する。
# push は「直近120分以内にこのマーカーが更新されている」ことを guard-command.sh が要求する。
#
# 使い方: bash .claude/hooks/verify.sh
set -u
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" || { echo "[verify] プロジェクトディレクトリへ移動できません"; exit 1; }

echo "[verify] legacy backend: pytest ..."
docker compose exec -T backend pytest -q || { echo "[verify] legacy FAILED — マーカー未更新"; exit 1; }
echo "[verify] EMPS new_backend: pytest ..."
docker compose exec -T new_backend pytest -q || { echo "[verify] EMPS FAILED — マーカー未更新"; exit 1; }

touch "$PROJECT_DIR/.claude/.tests-passed"
echo "[verify] OK — 両システム pass。マーカー更新: .claude/.tests-passed（以後120分間は push 可）"
