@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0skillwiki-launcher\scripts\Restore-SkillWikiDemoState.ps1"
echo.
echo Press any key to close this restore window. SkillWiki keeps running in the background.
pause >nul
