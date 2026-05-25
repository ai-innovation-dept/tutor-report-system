# 家庭教師 指導実績報告システム 停止スクリプト

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 指導実績報告システム 停止中..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Set-Location "C:\PerformanceReportingSystem\tutor-report-system"

docker compose down

Write-Host "`n停止完了 ✓" -ForegroundColor Green
pause
