# CS2 搬磚分析 — 啟動 Chrome（除錯模式）
# 右鍵 → 用 PowerShell 執行
# 或複製貼上到 PowerShell 視窗中執行

$chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
$args = @(
    "--remote-debugging-port=9222",
    "--remote-allow-origins=*",
    "--user-data-dir=C:\Users\user\AppData\Local\Google\Chrome\User Data"
)

Write-Host "正在啟動 Chrome（除錯模式）..." -ForegroundColor Green
Start-Process -FilePath $chrome -ArgumentList $args
Write-Host "Chrome 已啟動！請登入 FB 並打開土星社團" -ForegroundColor Green
Write-Host "此視窗可以關閉，不影響背景運作" -ForegroundColor Yellow
Start-Sleep -Seconds 3
