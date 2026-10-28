# 重新生成桌面快捷方式（如需重建 .lnk 时运行本脚本）
$ErrorActionPreference = 'Stop'
$dir  = 'C:\Users\刘喆\Desktop\搜韵典故每日打卡'
$html = Join-Path $dir 'index.html'
$lnk  = Join-Path ([Environment]::GetFolderPath('Desktop')) '搜韵典故每日打卡.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $html
$sc.WorkingDirectory = $dir
$sc.Description      = '搜韵典故每日打卡'
$sc.Save()
if (Test-Path $lnk) { 'OK ' + $lnk } else { 'FAIL' }
