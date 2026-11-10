# 一键：抓 Token → 报名
# 用法（同 l.md 里的命令）：
#   powershell -ExecutionPolicy Bypass -File "F:\GIT\lecture-booking\launcher.ps1"
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host "=== 第 1 步：抓 Token ===" -ForegroundColor Cyan
Write-Host "代理就绪后会自动拉起「科大微门户」小程序；多账号在微信里切换后重新打开小程序即可。" -ForegroundColor DarkGray
Write-Host "全部捕获完成后 Ctrl+C 退出（系统代理会自动恢复）。" -ForegroundColor DarkGray
python get_token_v1.py

Write-Host ""
Write-Host "=== 第 2 步：运行报名 ===" -ForegroundColor Cyan
python main.py

Write-Host ""
Write-Host "完成，回车关闭窗口..."
[void](Read-Host)
