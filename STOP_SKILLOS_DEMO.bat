@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%skillos-one-click-launcher\scripts\Stop-SkillOSDemo.ps1"
endlocal
