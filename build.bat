@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo Building the antivirus-conscious Nuitka standalone release...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_nuitka.ps1" %*
if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Build completed. See release\ for the folder, ZIP, and SHA-256 file.
exit /b 0
