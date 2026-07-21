@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo release.bat now builds the same verified Nuitka artifact as CI.
echo Push a matching vMAJOR.MINOR.PATCH tag to publish it automatically.
echo.
call "%~dp0build.bat" %*
exit /b %errorlevel%
