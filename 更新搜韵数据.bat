@echo off
chcp 936 >nul
cd /d "%~dp0"
set PY=D:\phthon\python.exe
if not exist "%PY%" set PY=python
echo ==== 搜韵典故数据更新 ====
echo 仅读取搜韵公开页面，不提交账号信息。
echo.
echo 常用参数：
echo    直接回车        抓「典故数 ^>= 3」的人物（含出处与典籍原文）
echo    --fill          只补抓缺少典籍原文的人物（增量补数据，推荐）
echo    --min 1         抓全部 2513 位人物（耗时很长）
echo    --jobs 1        单线程（默认，搜韵对并发敏感，建议保持 1）
echo.
echo 搜韵有访问频率限制，脚本遇到限流会自动放慢并重试，请耐心等待。
echo.
"%PY%" "%~dp0更新搜韵数据.py" %*
echo.
echo 完成。按任意键关闭窗口。
pause >nul
