# 家庭教師 指導実績報告システム 起動スクリプト

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 指導実績報告システム 起動中..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# プロジェクトフォルダに移動
Set-Location "C:\PerformanceReportingSystem\tutor-report-system"

# Docker Desktop が起動しているか確認
Write-Host "`n[1/3] Docker Desktop の確認..." -ForegroundColor Yellow
$dockerStatus = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop が起動していません。起動してください。" -ForegroundColor Red
    Write-Host "起動後、このスクリプトを再実行してください。"
    pause
    exit 1
}
Write-Host "Docker Desktop: 起動中 ✓" -ForegroundColor Green

# コンテナを起動
Write-Host "`n[2/3] コンテナを起動中..." -ForegroundColor Yellow
docker compose up -d

if ($LASTEXITCODE -ne 0) {
    Write-Host "コンテナの起動に失敗しました。" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "コンテナ: 起動完了 ✓" -ForegroundColor Green

# 起動完了を待機
Write-Host "`n[3/3] サービスの起動を待機中..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# 起動確認
$response = docker compose ps
Write-Host $response

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host " 起動完了！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " アプリ:    http://localhost:8000" -ForegroundColor White
Write-Host " API仕様:   http://localhost:8000/docs" -ForegroundColor White
Write-Host " メール確認: http://localhost:8025" -ForegroundColor White
Write-Host ""
Write-Host "ログイン情報:"
Write-Host "  管理者:   master1@example.com / Passw0rd!"
Write-Host "  受付:     receiver1@example.com / Passw0rd!"
Write-Host "  再鑑者:   reviewer1@example.com / Passw0rd!"
Write-Host "  講師1:    tutor1@example.com / Passw0rd!"
Write-Host "  講師2:    tutor2@example.com / Passw0rd!"
Write-Host ""

# ブラウザを自動で開く
Start-Process "http://localhost:8000"

pause
