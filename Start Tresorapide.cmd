@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "POWERSHELL_SCRIPT=%SCRIPT_DIR%scripts\start_tresorapide.ps1"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%POWERSHELL_SCRIPT%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo Tresorapide did not start successfully.
    echo Review the messages above, then press any key to close this window.
    pause >nul
)

exit /b %EXITCODE%
