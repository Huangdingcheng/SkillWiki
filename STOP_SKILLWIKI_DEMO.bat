@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%skillwiki-launcher\scripts\Stop-SkillWikiDemo.ps1"
endlocal
