# Manager 完整生命週期研究與優化報告

日期：2026-07-19

## 結論

Manager 已具備安裝環境、啟停前後端、程序輸出、基本存活監控、更新、設定與關閉處理，但原本不是完整的服務控制面：模型只能在功能首次執行時被動下載；模型檢查只判斷 `snapshots/` 是否非空；程序啟動成功被過早視為服務可用；設定更新、監督意圖與實際程序狀態也沒有完整同步。

本次已完成模型預下載、存在性檢查、結構化下載進度與取消，也補齊 HTTP readiness/health supervision、安全首次部署、持久化日誌、PID 接管與 Git 更新前置檢查。Manager 現在能區分「程序已建立」與「服務已就緒」，在 GUI 關閉後保留服務，並於下次啟動驗證 PID、建立時間、命令、工作目錄與健康端點後重新接管。原先盤點出的 P0/P1 與主要 P2 生命週期缺口均已處理。

## 生命週期盤點

```mermaid
flowchart TD
    A[launch.py] --> B{打包版且不在專案內?}
    B -->|是| C[首次下載專案]
    B -->|否| D[檢查 Manager GUI 依賴]
    C --> D
    D --> E[ManagerApp 建立視窗與讀取設定]
    E --> F[ProcessManager 建立前後端程序定義]
    F --> G[背景偵測 GPU、網路、環境、模型]
    F --> R{有可接管 PID 狀態?}
    R -->|是且驗證通過| S[接管既有服務並續讀持久化日誌]
    F --> H[啟動健康監督執行緒]
    E --> I{auto_start_on_launch}
    I -->|是| J[預檢 .venv / node_modules]
    J --> K[Popen 前後端並串流輸出]
    K --> Q[HTTP readiness 成功]
    Q --> L[HTTP 健康監督與異常重啟]
    E --> M[安裝、更新、模型下載、設定操作]
    L --> N[關閉視窗]
    M --> N
    N --> O{停止服務或保留服務}
    O --> P[停止監督執行緒並結束 GUI]
```

### 1. Bootstrap 與首次部署

- `launch.py` 在原始碼模式檢查 `ttkbootstrap`、`python-dotenv`、`psutil`，缺少時使用目前 Python 安裝。
- 打包版若不在專案目錄，會要求使用者選擇父目錄，再以 `git clone --depth 1` 建立全新的 `Omni-AI-GUI`；不再對未知或非空目錄執行 `git init`、`fetch`、`reset --hard`。
- 打包後的 EXE 仍從原位置載入 `_internal`，並透過 `--project-dir` / `OMNI_AI_PROJECT_ROOT` 指向外部專案，避免把 EXE 當成已搬移到 clone 目錄。
- Manager 的 `.venv` 與實際 Backend 執行環境分離；這個邊界是合理的，GUI 即使 Backend 環境損壞仍可修復它。
- 現況已能辨識「`.venv/python.exe` 檔案仍在，但基礎 Python 已移除」的損壞環境。

### 2. GUI 初始化

- 載入並合併 `manager_config.json`。
- 建立狀態、控制、Console、安裝/更新、設定、系統資訊分頁。
- 建立 `ProcessManager`、啟動背景系統偵測與健康監督。
- 依設定排程自動啟動 Backend/Frontend。

### 3. 環境建置與更新

- Python：建立 `.venv`、依 GPU 平台產生暫存 requirements、安裝依賴。
- Frontend：檢查 Node/npm 並執行 npm install。
- FFmpeg：獨立下載與解壓。
- Git：檢查更新與 pull。
- 完整重裝：停止服務、移除 `.venv`/`node_modules`、重新建置。

### 4. 程序啟動、運行、異常與重啟

- Backend 以 `.venv/python -m uvicorn backend.app:app` 啟動。
- Frontend 以 `npm run dev` 啟動，連接資訊由環境變數傳入 Vite。
- Backend 提供 `/health/live` 與 `/health/ready`；Manager 只有在 HTTP readiness 成功後才切換為 Running。執行中連續健康失敗達門檻才重啟，避免單次抖動造成重啟風暴。
- stdout/stderr 合併寫入 `.manager/logs/backend.log`、`frontend.log`，同時由背景執行緒追蹤並顯示於 Console；單檔達 10 MiB 會保留一份輪替檔。
- 本次新增「期望運行」狀態，將使用者意圖與 PID/畫面狀態分離。程序異常退出後仍可由 supervisor 判斷應否重啟；手動停止則不再被誤重啟。
- 啟停操作已序列化，避免快速重複點擊產生兩個相同服務。
- 連續重啟達上限後會停止監督該程序，而不是每個週期重複報錯。
- 終止時先對新程序群組發送可處理的終止訊號，超時才強制終止程序樹。

### 5. 設定生命週期

- 設定載入會驗證布林值、連接埠、時間與行數範圍。
- 設定改用暫存檔、flush/fsync、`os.replace` 原子更新，降低中斷寫入造成 JSON 損壞的風險。
- 儲存後立即重載 ProcessManager 的命令與環境；新設定於下次啟動或重啟套用，不必關閉 Manager。
- 新增啟動就緒逾時、健康探針逾時與連續失敗門檻；非法值會回復安全預設。

### 6. 關閉生命週期

- 關閉時若服務仍運行，使用者可選擇停止全部、保留服務或取消。
- 停止全部會先停止 supervisor，再終止子程序。
- 本次加入 supervisor thread 的有限 join，避免 GUI 銷毀後背景監控仍短暫操作狀態。
- 選擇保留服務時，子程序直接持有持久化日誌檔，不依賴 GUI 的 pipe；Manager 以原子 JSON 保存 PID 與建立時間。
- 下次啟動只接管 PID、建立時間、服務命令、工作目錄、日誌路徑與 HTTP 健康皆相符的程序；stale/偽造或已失效的紀錄只會忽略，不會誤殺未知程序。

## 模型生命週期

```mermaid
flowchart LR
    R[共享 model_registry] --> P{模型來源}
    P -->|Hugging Face| C[Hub snapshot 完整性檢查]
    P -->|PaddleX| X[official_models 推論檔檢查]
    R --> D[Manager 選擇模型]
    D --> W[.venv model_downloader]
    W --> Q{下載 provider}
    Q -->|Hugging Face| S[snapshot_download 完整倉庫]
    Q -->|PaddleX| Y[create_model 下載官方推論模型]
    S --> V[local_files_only 驗證]
    Y --> X
    V --> C
    C --> API[/api/system/status]
    C --> UI[Manager 已存在/缺少/不完整]
    X --> API
    X --> UI
    R --> E[ASR / OCR / CLIP / Semantic 載入器]
```

### 已納管模型

| 功能 | 模型 |
|---|---|
| ASR | `Qwen/Qwen3-ASR-1.7B-hf`、`Qwen/Qwen3-ASR-0.6B-hf` |
| 時間對齊 | `Qwen/Qwen3-ForcedAligner-0.6B-hf` |
| 語者分離 | `pyannote/speaker-diarization-community-1`（需要 token 與授權） |
| 以圖搜頁 | `openai/clip-vit-large-patch14` |
| Semantic | `BAAI/bge-reranker-v2-m3`、`BAAI/bge-m3` |
| OCR | `zai-org/GLM-OCR` |
| OCR 版面 | PaddleX `PP-DocLayoutV3`（選用） |

### 存在性判定

原判定只要 `snapshots/` 內有任何項目即回傳 `cached=true`，中斷下載、空 snapshot、損壞 symlink 或 `refs/main` 指向不存在 revision 都可能被誤判。本次改為：

1. Hugging Face 模型依 `HF_HUB_CACHE`、舊版 `HUGGINGFACE_HUB_CACHE`、`HF_HOME`、預設目錄順序解析快取。
2. 驗證模型 repo、snapshot、可讀檔案與 `refs/main` 指向。
3. 掃描 `*.incomplete` 與 broken symlink。
4. 回傳 `ready`、`partial`、`missing`，並保留舊版 API 的 `cached` 布林欄位。
5. Hugging Face 主動下載使用 `snapshot_download()` 取得完整 repository；完成後再以 `local_files_only=True` 驗證。
6. PP-DocLayoutV3 不誤用 Hugging Face 快取；改由 PaddleX 公開的 `create_model()` 下載到 GLM-OCR 實際使用的 `PADDLE_PDX_CACHE_HOME/official_models`，並檢查推論設定、權重與暫存檔。
7. 下載工作器以 JSONL 事件回報模型、已完成位元組、總位元組、百分比與驗證階段；舊版 Hub 無法預估總量時改顯示不確定進度。
8. GUI 可取消目前工作器；已下載的 Hub blob 保留，下一次可續用快取，不會把部分下載誤標成完成。

Hugging Face 官方說明指出，Hub cache 由 `refs`、`blobs`、`snapshots` 組成；`snapshot_download()` 用於下載整個 revision，Windows 不支援 symlink 時會直接把檔案存入 snapshot。本實作同時相容兩種布局。新版 Hub 也能利用 cached file list 對不完整 snapshot 產生錯誤；本地檔案檢查則提供舊版相容的第二層防線。

參考資料：

- [Hugging Face：Downloading files](https://huggingface.co/docs/huggingface_hub/package_reference/file_download)
- [Hugging Face：Understand caching](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache)
- [GLM-OCR 官方 GitHub](https://github.com/zai-org/GLM-OCR)
- [PaddleX：Layout Analysis / PP-DocLayoutV3](https://paddlepaddle.github.io/PaddleX/latest/en/module_usage/tutorials/ocr_modules/layout_analysis.html)

## 缺口、風險與處置

| 優先級 | 缺口 | 影響 | 本次狀態 |
|---|---|---|---|
| P0 | 無法預先下載模型 | 第一次執行功能才下載，長任務容易超時或失敗 | 已完成 |
| P0 | 模型快取僅檢查非空目錄 | 中斷下載可能被誤判為存在 | 已完成 |
| P1 | 模型 ID 分散硬編碼 | ASR/OCR/狀態清單容易漂移 | 已完成，共享 registry |
| P1 | 快速重複啟停可競爭 | 可能產生重複程序或錯誤狀態 | 已完成，操作序列化 |
| P1 | PID 狀態與使用者運行意圖混用 | 意外退出與手動停止難以正確監督 | 已完成，新增 desired state |
| P1 | 儲存設定後命令仍是舊值 | 需要重開 Manager 才能換 port/host | 已完成，熱重載命令 |
| P1 | 設定直接覆寫 JSON 且缺乏驗證 | 中斷或非法值會破壞下次啟動 | 已完成，驗證與原子寫入 |
| P1 | `Popen` 成功即顯示 Running | 服務可能仍在啟動或已無法 HTTP 回應 | 已完成，HTTP readiness |
| P1 | 打包版首次部署執行 `git reset --hard` | 目錄內既有檔案可能被覆蓋 | 已完成，全新目錄 shallow clone |
| P2 | 模型下載只有串流文字 | 無總位元組百分比、階段與取消 | 已完成，JSONL 事件與取消 |
| P2 | 健康監控只看 PID | deadlock 或 HTTP 掛起無法發現 | 已完成，連續失敗門檻與 `/health/*` |
| P2 | Console 只存在 GUI 記憶體 | 關閉 GUI 後無法診斷保留服務 | 已完成，持久化與輪替日誌 |
| P2 | Git pull 未與 dirty worktree/服務運行完整協調 | 更新可能衝突或載入新舊混合程式 | 已完成，dirty/running preflight |
| P3 | 保留服務後關閉 GUI 無法重新接管既有 PID | 下次開啟可能因 port 衝突而失敗 | 已完成，PID/create-time/command/health 驗證接管 |

## 驗證結果

- Manager/model/ASR/OCR 針對性測試共 42 項：40 通過，2 項因隔離 runtime 未安裝 FastAPI、官方 glmocr 而明確跳過，無失敗。
- Manager headless 測試使用臨時 HTTP server 驗證 wildcard URL、readiness 成功、readiness timeout、持久化日誌、接管與停止契約、命令白名單、設定原子存取、dirty worktree 阻擋更新。
- 模型測試涵蓋 Hugging Face 與 PaddleX 的 missing/ready/partial/broken-ref、cache 優先序、provider 派送、registry 唯一性、JSONL event parser、取消控制器與損壞 `.venv` 阻擋下載。
- Python 語法編譯通過。
- Frontend production build 通過（Vite 6.4.1，57 modules）。
- 全套 discovery 仍會載入既有的手動整合腳本：`test_auth.py`、`test_email.py` 在此隔離 runtime 缺少 `requests`；`test_merge.py`、`test_split.py` 會在 CP950 import 階段輸出 emoji。這四項不是本次 Manager regression，正式專案依賴已包含 `requests`。
- 目前專案 `.venv` 不可執行，指向已移除的 `C:\Users\zx020\AppData\Local\Programs\Python\Python312\python.exe`；實機模型權重下載與完整 Backend 啟動需先由 Manager 重建 `.venv`。

## 後續維護原則

1. 變更服務啟動參數時，同步更新 readiness contract 與 headless lifecycle tests。
2. 新增本機模型時只修改共享 `model_registry`，並為 provider 補上下載與 cache 驗證測試。
3. 發佈前在已重建的 Python 3.12 `.venv` 執行一次 Backend live/ready smoke test，以及至少一個小型模型的實際下載/取消/續傳測試。
4. 若要讓全套 `unittest discover` 成為 CI gate，應把四個既有手動腳本改為可明確跳過的整合測試，並強制測試程序使用 UTF-8 console。
