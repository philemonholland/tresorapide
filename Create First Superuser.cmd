@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "POWERSHELL_SCRIPT=%SCRIPT_DIR%scripts\create_first_superuser.ps1"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%POWERSHELL_SCRIPT%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The first superuser was not created.
    echo Review the messages above, then press any key to close this window.
    pause >nul
)

exit /b %EXITCODE%
