@echo off
chcp 936 >nul
cd /d "%~dp0"
set PY=D:\phthon\python.exe
if not exist "%PY%" set PY=python
echo ==== 补齐卡片「出处」中的典籍原文 ====
echo 只补抓缺少典籍原文的人物，已抓到的自动跳过（断点续抓，可随时关闭）。
echo 仅读取搜韵公开页面，不提交账号信息。搜韵有限流，脚本会自动冷却重试。
echo.
"%PY%" "%~dp0更新搜韵数据.py" --fill
echo.
echo 完成。重新打开或刷新网页，即可看到补齐后的出处原文。
pause >nul
