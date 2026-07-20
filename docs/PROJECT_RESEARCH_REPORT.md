# Omni AI Manager 專案生命週期、建置與優化研究報告

研究日期：2026-07-20（Asia/Taipei）  
範圍：repository 結構、Manager／Backend／Frontend 生命週期、依賴與建置、測試、發布、安全與 GitHub 維護成熟度。

## 執行摘要

專案已形成清楚的三層產品：ttkbootstrap Manager 擁有環境、模型與子程序生命週期；FastAPI 負責本機多模態推論和 SQLite 任務狀態；React/Vite 提供登入後工作台。其核心優點是模型下載與推論分權、長任務有取消／逾時／終態、Manager 使用 readiness 而非單看 PID，以及模型 cache 有完整性判斷。

本次基線發現三個工程缺口：現有 `.venv` 綁定已移除的 Python 而不可執行；PowerShell policy 使 `npm.ps1` 不能使用但 `npm.cmd` 可正常建置；76 項離線單元測試中 1 項受本機 `OCR_DEVICE` 汙染而失敗。Repository 亦缺少正式 MIT 檔、可執行的 GitHub CI、維護政策與一致的建置／發布文件，原 `release.bat` 會自動 stage、commit、push 全部工作樹，存在誤發布風險。

完成的優化包含：ASR 媒體範圍選取、sample-accurate 裁切、Windows process-tree 終止、Manager widget 相容、可重現的 `npm ci`、固定 Transformers commit、AI 推論路由 JWT、密碼學安全 OTP、測試環境隔離、安全的 release preflight，以及完整 GitHub 文件／templates／CI。最終驗證結果記錄於本報告最後一節。

## 研究方法與證據

- 盤點 Git tracked/untracked、歷史 tag/commit、入口點、依賴、routes、tests 與既有專項報告。
- 讀取 `launch.py`、Manager environment/process/model modules、FastAPI lifespan/routes/database/providers、React routes/context 與 Vite proxy。
- 執行 Python `compileall`、明確列舉的離線 unittest suite、Vite production build 與 `git diff --check`。
- 不把需下載大型模型、真實 GPU、SMTP 或 YouTube 的歷史結果冒充本次實測；這些能力的既有驗證見各專項報告。

## 完整生命週期

### 取得與 bootstrap

Source 模式由 `python launch.py` 啟動；PyInstaller 模式若不在完整 project tree，可讓使用者選擇位置並 shallow clone。外部 project root 必須同時含 Manager、Backend 與 Frontend 核心檔，避免在錯誤目錄執行。clone 在背景 thread 進行，不阻塞 Tk UI。

### 環境安裝

Manager 尋找 Python 3.10–3.13並偏好 3.12，執行版本 probe 判斷 `.venv` 是否健康。Python dependencies 會依 GPU 偵測替換 PyTorch index；Frontend 有 lockfile 時使用 `npm ci`；FFmpeg 可使用 PATH 或下載 Windows bundle。環境是機器本機資產，不可攜。

### 設定與模型

`.env` editor 依 schema 補安全預設、驗證必填與條件欄位；`manager_config.json` 驗證服務 port、timeout、restart policy。模型 registry 統一 Manager 與 Backend ID，Manager 明確下載並驗證 snapshot；Backend 設定 local-only，缺模型時回報由 Manager 處理。

### 服務執行

Backend lifespan 初始化 DB、修復上次中斷任務、背景載入 semantic models，再提供 core readiness；Manager 等待 HTTP ready 後才標示 running。Frontend 以 Vite proxy 對 Backend 同源存取。健康檢查連續失敗時依有界 policy 重啟，log 與 process record 存入 `.manager/`。

### 功能任務

ASR／YouTube 任務保存至 SQLite，背景工作以全域鎖保護單 GPU runtime，透過 SSE 報告進度，支援取消、逾時、失敗與重啟中斷終態。OCR／CLIP／workflow／semantic 為同步或 provider 管線，現在與任務路由一樣要求 JWT。Frontend 負責提交、進度、編修與匯出，不是權威狀態來源。

### 關閉、更新與發布

Manager 結束完整 Windows process tree，Backend shutdown 清理 semantic worker。Git update 會拒絕 dirty worktree，避免覆蓋使用者變更。Release 現在只從乾淨且已審核的 HEAD 建置與建立 GitHub Release，不再自動提交或 push。

## 建置可重現性評估

| 項目 | 結論 | 改善 |
| --- | --- | --- |
| Python runtime | 支援範圍明確，但 `.venv` 依賴原 base path | Manager health probe 與重建文件 |
| Python packages | Torch 固定；部分 packages 仍為寬鬆版本 | Transformers 固定驗證 commit；保留 lockfile/constraints 為後續工作 |
| Frontend | `package-lock.json` 已存在 | Manager 改用 `npm ci`，CI 亦採 `npm ci` |
| Models | 巨大且不適合 Git | registry + snapshot verification + local-only inference |
| FFmpeg | Windows bundle 非 Git 資產 | Manager 管理，文件列明上游授權 |
| Manager artifact | PyInstaller onedir | Release preflight、乾淨 commit、smoke-test checklist |
| Version | API、npm、tag 尚有多來源 | 文件明列為後續單一版本來源工作 |

## 風險與優先級

| 等級 | 風險 | 現況／建議 |
| --- | --- | --- |
| P0 | Secret、媒體、DB 或模型被提交 | `.gitignore` 已擴充；發布前仍需 `git status` 與 secret scan |
| P1 | 公網濫用 GPU／OTP | 推論 JWT 已補；仍需 proxy TLS、rate limit、短效 token/revoke |
| P1 | Background thread/SQLite 無法跨程序續跑 | 單機可用；多 worker 必須改 durable queue + external DB/lock |
| P1 | 依賴 main 漂移破壞 ASR/OCR | Transformers 已 pin；其餘建議產生平台 constraints/SBOM |
| P1 | 多小時真實推論 OOM／耗時不可控 | 已有 streaming、global lock、cancel、timeout；仍需正式壓測基準 |
| P2 | Query token 出現在 log/history | 受瀏覽器 API 限制；公網應改一次性 URL 或受控 proxy |
| P2 | 版本資訊不一致 | 建議建立單一 `VERSION` 並由 build 讀取 |
| P2 | 只有 Windows release automation | 目前產品定位一致；若擴大平台需建立矩陣與 artifact signing |

## 本次程式與工程優化

- ASR 新增 start/end time，API 先驗證 finite、非負與順序，再傳到 engine。
- PyAV resample 後以 16 kHz sample count 精確裁切，避免只靠 seek 造成邊界偏差。
- Frontend 新增媒體 range player 與響應式／可及性樣式。
- Windows 使用 `taskkill /F /T` 對 live root PID 原子終止 npm/node/Vite tree。
- ttk LabelFrame padding 移到 child Frame，兼容不同 Tk widget 實作。
- OCR test 明確固定 `OCR_DEVICE=auto`，不受開發機 `.env` 影響。
- OCR、CLIP、workflow、semantic route 套用與 tasks 相同的 JWT dependency。
- Email OTP 由 `random` 改為 `secrets`。
- npm install 在 lockfile 存在時改為 `npm ci`。
- Transformers 固定到既有 Qwen3-ASR 實測的官方 commit。
- release script 移除自動 stage/commit/push，加入工具、venv、dirty tree、既有 tag 與每階段錯誤檢查。
- 補齊 MIT、README、架構／設定／開發／發布／研究、治理 templates、CI、Dependabot、EditorConfig 與 attributes。

## 驗收結果與尚未執行

本次最終驗證：

- Python 3.12 `compileall`：通過。
- 明確列舉的離線 suite：78/78 通過，耗時約 19 秒；未下載模型權重。
- Vite 6.4.3 production build：通過，59 modules transformed；輸出 JS 約 342.09 kB（gzip 103.29 kB）、CSS 約 39.04 kB（gzip 7.55 kB）。
- Repository runtime artifact 掃描：沒有追蹤 `.env`、DB、uploads、results、`.manager`、`.venv`、node_modules 或 dist。
- 文件與 GitHub config manifest：通過；`git diff --check` 通過。

執行環境原 `.venv` 的 launcher 綁定已移除的 Python 3.12，故測試使用同版可用 Python 搭配既有 site-packages；這正是文件要求「搬移／移除 base Python 後重建 venv」的實際案例。Frontend 初次在 sandbox 讀取 pnpm layout 遇到 EPERM，允許正常本機讀取後 build 通過，非原始碼錯誤。

真實模型 CUDA、2 小時以上音訊、多人 diarization、YouTube 網路中斷重試、SMTP deliverability、PyInstaller clean-machine smoke test與公開網路滲透／負載測試不在本次執行範圍，發布前仍應依 [RELEASE.md](RELEASE.md) 驗收。

## 後續路線圖

1. 建立單一版本來源並自動同步 FastAPI、Frontend 與 Release。
2. 為 Python 依賴建立 Windows CPU/CUDA constraints、SBOM 與 artifact checksum/signing。
3. 將手動 integration scripts 與 offline tests 分目錄／markers，加入可選 service test CI。
4. 將長任務搬到持久 queue，提供 restart resume、跨程序鎖與結構化 metrics。
5. 加入 OTP rate limiting、JWT revoke/rotation 與不使用 query token 的下載機制。
6. 建立真實硬體 benchmark：短音訊連續 50 件、5 件同時提交、2 小時音訊、多人語者、OCR 多頁 PDF 與 VRAM/RAM 峰值。
