# 開發與測試指南

## 建議環境

- Windows 10/11
- Python 3.12（程式允許 3.10–3.13）
- Node.js 20+ / npm
- Git；需要發布時另裝 GitHub CLI
- 選配 NVIDIA GPU 與相容 driver

不要搬移或提交 `.venv`。若其 `python.exe` 回報找不到建立時的系統 Python，請由 Manager 重建。

## 建立環境

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Set-Location frontend
npm.cmd ci
```

`requirements.txt` 預設 PyTorch index 為 CUDA 12.8。Manager 會依 GPU 偵測產生暫時 requirements，CPU 手動安裝則應改用 PyTorch CPU index。Transformers 使用固定官方 commit，因專案同時需要尚未進入穩定版的 Qwen3-ASR 原生支援與 GLM-OCR 所需的 Transformers 5.x。

## 啟動

最貼近使用者流程：

```powershell
python launch.py
```

Backend 除錯：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Frontend 除錯：

```powershell
Set-Location frontend
$env:BACKEND_HOST = "127.0.0.1"
$env:BACKEND_PORT = "8000"
npm.cmd run dev -- --host localhost --port 5173
```

## 測試策略

| 層級 | 目的 | 是否需模型／外部服務 |
| --- | --- | --- |
| Compile | 所有 Python 檔語法與 import-time parse | 否 |
| Frontend build | JSX、Vite graph 與 production bundle | 否（需已安裝 npm deps） |
| Offline unit | Manager、cache、provider contract、ASR lifecycle、API guard | 否；模型均 mock |
| Service integration | JWT、DB、HTTP、SSE、媒體與健康檢查 | 需要本機服務 |
| Hardware/model | CUDA、真實權重、長音訊、語者分離、OCR | 是 |
| External integration | YouTube、SMTP、遠端 OCR provider | 是，且可能產生外部流量 |

快速檢查：

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend manager tests launch.py
Set-Location frontend
npm.cmd run build
```

離線 suite：

```powershell
.\.venv\Scripts\python.exe -m unittest -v `
  tests.test_manager_lifecycle `
  tests.test_model_management `
  tests.test_semantic_engine `
  tests.test_glmocr `
  tests.test_asr_stability `
  tests.test_asr_api
```

不要直接以一般 pytest discovery 當作完全離線 suite：`test_auth.py` 與 `test_email.py` 是會呼叫 localhost service／SMTP 的手動腳本。執行外部整合前請使用測試帳號並確認輸入與網路成本。

## 品質工具

```powershell
.\.venv\Scripts\python.exe -m ruff check backend manager tests launch.py
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
git diff --check
```

`pip-audit` 對 Git dependency 或 GPU-specific wheel 的解析可能需要額外處理；任何例外都應在 PR 記錄原因，不應靜默忽略。

## 常見問題

### `.venv` 無法建立 process

虛擬環境綁定的 base Python 已移除。重新安裝 Python 3.12，從 Manager 執行環境重建，或在停止服務後刪除 `.venv` 再建立。

### PowerShell 禁止 `npm.ps1`

使用 `npm.cmd`，不必更動整台機器的 execution policy。

### 模型顯示 missing/partial

由 Manager 重新下載／續傳並等待 snapshot 驗證完成。不要手動以空目錄或 config-only snapshot 假冒模型完成。

### Backend ready、Semantic 尚在 loading

Core API 已可服務，語意模型仍在背景初始化。查看 `/health/ready`、`/api/system/status` 與 `.manager/logs/`。

### 連接埠不一致

`manager_config.json` 的實際值會覆蓋程式預設；Vite proxy 同時必須收到相同的 `BACKEND_HOST/BACKEND_PORT`。
