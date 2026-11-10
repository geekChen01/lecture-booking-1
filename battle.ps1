# 卡点抢票：token 新鲜度检查 + 预热卡点模式
# 用法：
#   powershell -ExecutionPolicy Bypass -File battle.ps1
# 战斗日流程：11:50 先跑 launcher.ps1 抓 token → 11:55~11:59 跑本脚本 → 它自动等到开放时刻+0.1s 齐射
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host "=== 卡点抢票 ===" -ForegroundColor Cyan

# 1) token 新鲜度检查
$caches = Get-ChildItem auth\auth_cache_*.json -ErrorAction SilentlyContinue
if (-not $caches) {
    Write-Host "❌ 没有任何 token 缓存，请先运行 launcher.ps1 抓 token" -ForegroundColor Red
    Read-Host "回车退出"
    exit 1
}
$newest = ($caches | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
$age = (Get-Date) - $newest
Write-Host ("token 缓存 {0} 个，最新抓取于 {1:HH:mm:ss}（{2:N0} 分钟前）" -f $caches.Count, $newest, $age.TotalMinutes)
if ($age.TotalMinutes -gt 240) {
    Write-Host "⚠️ token 已超过 4 小时，大概率过期（实测寿命仅数小时）！" -ForegroundColor Red
    Write-Host "   建议先运行 launcher.ps1 重抓，再回来跑本脚本" -ForegroundColor Yellow
    $ans = Read-Host "仍要强行继续吗？(y/N)"
    if ($ans -ne 'y' -and $ans -ne 'Y') { exit 1 }
}

# 2) 预热卡点模式
Write-Host ""
Write-Host "说明：若有讲座即将开放（10 分钟内），将预热连接并等到开放时刻+0.1s 齐射；" -ForegroundColor DarkGray
Write-Host "      否则自动按普通模式运行（直接抢当前可报名的）。Ctrl+C 可随时中止。" -ForegroundColor DarkGray
Write-Host ""
python main.py --prewarm

Write-Host ""
Read-Host "完成，回车关闭窗口"
