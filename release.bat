@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo   Omni AI Manager - Build ^& Release Script
echo ============================================================
echo.

where git >nul 2>nul || (
    echo 錯誤：找不到 Git。
    exit /b 1
)
where gh >nul 2>nul || (
    echo 錯誤：找不到 GitHub CLI ^(gh^)。
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo 錯誤：找不到可用的 .venv，請先執行 python launch.py 建立環境。
    exit /b 1
)
.venv\Scripts\python.exe --version >nul 2>nul || (
    echo 錯誤：.venv 已損壞，請由 Manager 重建環境。
    exit /b 1
)
for /f %%i in ('git status --porcelain') do (
    echo 錯誤：工作樹有未提交變更；Release 必須由已審核的乾淨 commit 建置。
    exit /b 1
)

set /p "VERSION=請輸入版本號 (例如 v1.2.0): "
if "%VERSION%"=="" (
    echo 錯誤：版本號不能為空！
    exit /b 1
)
git rev-parse "%VERSION%" >nul 2>nul && (
    echo 錯誤：Tag %VERSION% 已存在。
    exit /b 1
)

set /p "NOTES=請輸入 Release 說明 (可留空): "
if "%NOTES%"=="" set "NOTES=Release %VERSION%"

echo.
echo   版本號: %VERSION%
echo   說明:   %NOTES%
echo.
set /p "CONFIRM=確定要從目前 commit 建置並發布嗎？ (y/N): "
if /i not "%CONFIRM%"=="y" (
    echo 已取消。
    exit /b 0
)

echo.
echo [1/4] 正在安裝管理面板建置依賴...
.venv\Scripts\python.exe -m pip install -r manager\requirements.txt pyinstaller
if errorlevel 1 (
    echo 建置依賴安裝失敗！
    exit /b 1
)

echo.
echo [2/4] 正在使用 PyInstaller 建置...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name "Omni-AI-Manager" --add-data "manager;manager" --hidden-import ttkbootstrap --hidden-import dotenv --hidden-import psutil launch.py
if errorlevel 1 (
    echo PyInstaller 建置失敗！
    exit /b 1
)
echo PyInstaller 建置完成

echo.
echo [3/4] 正在壓縮為 zip...
if exist dist\Omni-AI-Manager.zip del /f dist\Omni-AI-Manager.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Omni-AI-Manager' -DestinationPath 'dist\Omni-AI-Manager.zip' -Force"
if errorlevel 1 (
    echo 壓縮失敗！
    exit /b 1
)
echo 壓縮完成

echo.
echo [4/4] 正在發布 GitHub Release...
gh release create "%VERSION%" "dist\Omni-AI-Manager.zip" --target HEAD -t "Omni AI Manager %VERSION%" -n "%NOTES%"
if errorlevel 1 (
    echo GitHub Release 發布失敗！本機建置檔仍保留於 dist。
    exit /b 1
)

echo.
echo ============================================================
echo   Release %VERSION% 發布成功！
echo ============================================================
exit /b 0
