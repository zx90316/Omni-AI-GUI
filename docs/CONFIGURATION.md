# 設定參考

設定分兩層：`.env` 控制 Backend、模型與驗證 Secret；`manager_config.json` 控制桌面 Manager 與服務監督。修改後通常需重啟相關服務。

## `.env`

從 `.env.example` 複製，不要提交實際檔案。

| 變數 | 預設／範例 | 說明 |
| --- | --- | --- |
| `OCR_PROVIDER` | `local` | `local`、`openai` 或 `ollama` |
| `OCR_MODEL` | `zai-org/GLM-OCR` | OCR 模型 ID／遠端服務模型名 |
| `OCR_DEVICE` | `auto` | `auto`、`cpu`、`cuda` 或 `cuda:N` |
| `GLMOCR_LAYOUT_DEVICE` | 空白 | Layout 模型裝置；空白由 runtime 決定 |
| `OCR_API_URL` | 空白 | OpenAI-compatible OCR completion endpoint；provider 為 `openai` 時必填 |
| `OCR_API_KEY` | 空白 | 遠端 OCR Token |
| `OCR_MAX_WORKERS` | `8` | OCR 文件工作數，範圍 1–128；GPU 記憶體不足時調低 |
| `OLLAMA_HOST` | `http://localhost:11434` | legacy Ollama host |
| `HF_TOKEN` | 空白 | gated model 下載授權；pyannote 通常需要 |
| `ASR_MAX_NEW_TOKENS` | `512` | 每段 generation 上限，範圍 64–8192 |
| `ASR_ATTN_IMPLEMENTATION` | 空白 | 選配 `eager`、`sdpa`、`flash_attention_2` |
| `ASR_MAX_UPLOAD_MB` | `2048` | ASR 上傳上限 MiB，範圍 1–102400 |
| `ASR_TASK_TIMEOUT_SECONDS` | `14400` | 排隊加執行 timeout，最少 60 秒 |
| `SECRET_KEY` | 無安全預設 | JWT HS256 key；至少 32 字元、每部署唯一 |
| `SMTP_HOST` | `smtp.gmail.com` | Email OTP SMTP host（Gmail／企業／自建皆可） |
| `SMTP_PORT` | `465` | SMTP 埠；允許 1–65535 |
| `SMTP_SECURITY` | `auto` | `ssl`／`starttls`／`none`；`auto` 依埠猜測（465→ssl、25→none、其餘→starttls） |
| `SMTP_USER` | 無 | 選填；信箱或純使用者名稱；開放 relay 可留空 |
| `SMTP_PASSWORD` | 無 | 選填；不需驗證時可留空 |
| `SMTP_FROM_EMAIL` | 無 | OTP 寄件者地址（必填） |

Manager 會拒絕缺少必要值、placeholder Secret、錯誤 Email、超出範圍的數值，以及 provider 相依欄位不完整的設定。`OCR_API_KEY`、`HF_TOKEN` 與 SMTP 密碼屬 Secret，不要貼入 Issue 或 log。

Hugging Face cache 可透過標準 `HF_HUB_CACHE`、`HUGGINGFACE_HUB_CACHE` 或 `HF_HOME` 調整，優先順序依此排列。Manager 與 Backend 必須使用同一組 cache 環境。

## `manager_config.json`

| 欄位 | 程式預設 | 合法範圍／說明 |
| --- | ---: | --- |
| `auto_restart` | `true` | 健康檢查失敗後是否自動重啟 |
| `auto_start_on_launch` | `true` | Manager 啟動後是否依下列開關啟動服務 |
| `start_backend` | `true` | 自動啟動 Backend |
| `start_frontend` | `true` | 自動啟動 Frontend |
| `detected_compute_platform` | 空白 | Manager 偵測的 `cpu`／CUDA wheel channel |
| `backend_host` | `0.0.0.0` | Backend bind host；僅本機建議 `127.0.0.1` |
| `backend_port` | `8000` | 1–65535 |
| `frontend_host` | `localhost` | Vite bind host |
| `frontend_port` | `5173` | 1–65535 |
| `health_check_interval` | `5` | 1–3600 秒 |
| `health_probe_timeout` | `2` | 1–30 秒 |
| `health_failure_threshold` | `3` | 1–20 次連續失敗 |
| `startup_timeout` | `60` | 5–600 秒 readiness 上限 |
| `restart_delay` | `3` | 0–3600 秒 |
| `max_restart_attempts` | `5` | 1–100 次連續重啟 |
| `console_max_lines` | `5000` | GUI 保留 100–100000 行 |
| `theme` | `darkly` | ttkbootstrap theme 名稱 |

未知或不合法值會在載入時回退為預設，儲存採 temporary file + atomic replace。Manager 對 `0.0.0.0` readiness probe 會改用 loopback，不會嘗試連線 wildcard address。

## Vite 開發環境

`frontend/vite.config.js` 讀取 `HOST`、`PORT`、`BACKEND_HOST` 與 `BACKEND_PORT`。Manager 啟動 Frontend 時會注入正確值；手動啟動時需自行保持與 Backend 一致。Vite 使用 `strictPort`，連接埠被占用時直接失敗，以便 Manager 正確判定狀態。

## 設定變更檢核

1. 先停止相關服務。
2. 由 Manager editor 修改並通過驗證。
3. 確認 bind address、連接埠、防火牆與 proxy 一致。
4. 重啟後檢查 `/health/live`、`/health/ready` 與 Frontend。
5. 不要將含 Secret 的 `.env` 納入 commit、備份截圖或支援附件。
