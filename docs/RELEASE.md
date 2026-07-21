# 發布流程

## 版本來源

產品版本以 `omni_version.py` 的 `__version__` 為唯一來源，採 `MAJOR.MINOR.PATCH`；`pyproject.toml` 動態讀取同一值。正式 tag 必須是相同版本加上 `v` 前綴，例如 `__version__ = "1.2.0"` 對應 `v1.2.0`。Tag、原始碼與 Windows executable 版本不一致時，workflow 會直接失敗。

## 發行內容

Windows Release 是 `Omni-AI-Manager-v<version>-windows-x64-standalone.zip`，只包含：

- Nuitka standalone Manager executable 與其必要 runtime files。
- 授權、安全與第三方聲明，以及 ZIP 的 SHA-256 checksum。

Backend、Frontend、AI Python runtime、模型權重、FFmpeg、`.env`、資料庫、uploads、results、cache、`.venv`、`node_modules` 與其他專案／本機資料都不包含在 ZIP。Manager 第一次啟動時讓使用者選擇位置並 clone 完整 repository。發行內容由 `packaging/release-manifest.json` allowlist 控制，建置後會再次驗證必要檔案與禁止項目。

Manager 會將最後一次驗證成功的 project root 記錄於使用者的 `%LOCALAPPDATA%\Omni-AI-Manager\bootstrap.json`，並優先偵測 EXE 同目錄及 `Omni-AI-GUI/` 子目錄。建置腳本若發現輸出資料夾內已有完整 clone，會拒絕清理；開發者應改用 `-OutputDirectory <path>` 建立隔離產物。

## 本機建置

本機 Manager build 需要 Windows 10/11 與 Python 3.10–3.13，建議 Python 3.12。Frontend 不進入產物；GitHub Release workflow 會另以 Node.js 22 執行 source build 驗證。

```powershell
# 完整 preflight、Nuitka build、自我檢查、ZIP 與 SHA-256
.\build.bat

# 等價的 PowerShell 入口
.\scripts\build_nuitka.ps1

# 公司正式簽章（需要 Windows SDK 與 CurrentUser\My 憑證）
.\scripts\build_nuitka.ps1 -CertThumbprint "<certificate SHA-1 thumbprint>"
```

建置腳本只提供 Nuitka `standalone`。專案不提供 onefile 或 UPX 選項，因為自解壓與 executable packing 會增加企業防毒啟發式規則的觸發面。

## 自動 GitHub Release

1. 更新 `omni_version.py`、CHANGELOG 與必要文件。
2. 執行測試與本機 build，確認工作樹只含預期變更。
3. Commit、review、merge 後建立相符 tag：

   ```powershell
   git tag v1.2.0
   git push origin v1.2.0
   ```

4. `.github/workflows/release.yml` 會在 Windows runner：

   - 驗證 tag 與 source version。
   - 建置 Frontend 並執行 packaging tests。
   - 使用 Nuitka standalone 建置完整發行包。
   - 若已設定 repository secrets，套用 Authenticode。
   - 執行 executable standalone 自我檢查與外部 project import smoke test。
   - 產生 ZIP、SHA-256 與 GitHub provenance attestation。
   - 建立或更新相同 tag 的 GitHub Release 並上傳資產。

也可從 Actions 手動執行；輸入版本仍必須與 `omni_version.py` 相同。

### CI Authenticode secrets

正式公司發行可設定：

- `WINDOWS_CERTIFICATE_BASE64`：PFX 的 Base64 內容。
- `WINDOWS_CERTIFICATE_PASSWORD`：PFX 密碼。

沒有憑證時 workflow 仍會產生未簽章 release，但企業部署前應由組織簽章或由資安單位核准雜湊。憑證私鑰不得提交至 repository 或包含在 artifact。

## 驗收

- 在乾淨 Windows VM 完整解壓 ZIP，不能只取出 executable。
- 執行 `scripts/verify_release.ps1` 或確認 workflow 的 verification step 通過。
- 比對 `.sha256`，或執行 `gh attestation verify <zip> -R zx90316/Omni-AI-GUI`。
- 確認 standalone Manager 可 clone 專案，再建立 `.venv`、執行 `npm ci`、儲存 `.env`、安裝 FFmpeg／模型並啟停 Backend/Frontend。
- 確認 `/health/live`、`/health/ready`、登入與至少一個 CPU smoke test。
- 在公司的 EDR policy 下測試已簽章成品；如有誤判，向廠商提交樣本並以 publisher/hash 進行最小範圍 allowlist。

任何工具都無法保證所有防毒引擎零誤判。請勿以停用防毒、關閉 SmartScreen 或排除整個使用者目錄作為發布方案。
