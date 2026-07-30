@echo off
REM CS2 搬磚分析專用 Chrome 捷徑
REM 這個批次檔會啟動 Chrome 並開啟遠端除錯端口，讓 Hermes 能在背景分析 FB 社團貼文
REM
REM 使用方法：雙擊執行即可，Chrome 會正常開啟，不影響你平常使用

start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --remote-allow-origins=* ^
  --user-data-dir="C:\Users\user\AppData\Local\Google\Chrome\User Data"

echo Chrome 已啟動（除錯模式），Hermes 可隨時連線分析。
echo 請保持 Chrome 開啟，不要關閉此視窗。
pause
