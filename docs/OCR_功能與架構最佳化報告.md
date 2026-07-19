# Omni-AI-Manager OCR 功能與架構最佳化報告

日期：2026-07-19

## 結論

舊版 OCR 可完成圖片/PDF 的逐頁全文辨識與自訂欄位萃取，但架構實際上被 Ollama 綁死，且把完整 GLM-OCR 上游倉庫（包含另一套前後端、範例、模型輸出與圖片）直接放進 `backend/glm-ocr`。UI 傳入的 `model`、`ollama_host`、`max_retries` 多數沒有真正作用；每辨識一頁還會重新建立整套 `GlmOcr` pipeline，效能與可維護性都不理想。

本次已改成：

1. 預設由 Omni-AI-Manager 程序內直接使用 Transformers 載入 `zai-org/GLM-OCR`，OCR 不再需要 Ollama。
2. 完整文件解析仍採官方 `glmocr` SDK，但由 PyPI 鎖版安裝，透過 in-process adapter 呼叫同一個本機模型，不需啟動另一個 HTTP/Ollama 程序。
3. 保留 OpenAI-compatible（vLLM/SGLang）與 Ollama provider，正式環境可按吞吐需求切換。
4. 移除 334 個上游複製檔案，共約 109.43 MiB；依賴改為 `glmocr[selfhosted]==0.1.5`。
5. OCR UI/API 擴增為完整文件、文字、表格、公式、資訊欄位萃取五種任務，並提供 Markdown/JSON、PDF 逐頁進度、多頁合併與模型卸載。

## 舊版現況盤點

### 實際處理流程

```text
OCR.jsx
  → POST /api/ocr/process (SSE)
  → process_file_stream 逐頁轉 PNG
  → 每頁 new GlmOcr(config_path=backend/config.yaml)
  → glmocr SDK
  → Ollama /api/generate
  → Markdown / 嘗試解析 JSON
```

### 舊版已有功能

| 功能 | 狀態 | 備註 |
|---|---|---|
| 圖片 OCR | 有 | PNG/JPG/BMP/TIFF/WEBP/GIF |
| PDF OCR | 有 | PyMuPDF 轉圖後逐頁處理 |
| SSE 進度 | 有 | 每頁事件 |
| 全文辨識 | 部分 | 使用非官方 `OCR` prompt |
| JSON 欄位萃取 | 有 | 僅預期頂層 object |
| 多頁欄位合併 | 有 | 值衝突時清空 |
| 簡轉繁與修正字典 | 有 | 僅遞迴處理到第一層 |
| 版面分析 | 未啟用 | `backend/config.yaml` 固定 `enable_layout: false` |
| 表格/公式獨立任務 | UI/API 無法選擇 | 上游設定存在，但沒有接到產品功能 |
| Ollama 以外推論 | 無 | 設定固定 `/api/generate` |

### 主要問題

1. **假參數與錯誤抽象**：`ollama_host`、`model`、`max_retries` 經 Router 傳入 Engine，但 `_call_glm_ocr` 不使用它們。使用者以為修改模型或重試設定有效，實際並沒有。
2. **每頁重建 pipeline**：每次 `_call_glm_ocr` 都建立 `GlmOcr`，若開啟 layout 會更昂貴；也沒有顯式 `close()`。
3. **同步推論阻塞 event loop**：舊 Router 在 async SSE generator 內直接迭代同步模型工作，辨識期間會阻塞其他 FastAPI 請求。
4. **上游原始碼整包複製**：`backend/glm-ocr` 有 334 個追蹤檔案、約 109.43 MiB，包含上游 GitHub 設定、另一套 Web App、Docker、範例 PDF/圖片、輸出結果和字型。專案真正需要的只是 Python SDK。
5. **依賴不可重現**：`-e backend/glm-ocr` 綁住一份舊版 0.1.1 原始碼；上游目前 PyPI 已為 0.1.5，兩者安裝方式與依賴需求不同。
6. **沒有完整官方任務**：GLM-OCR 正式文件解析 prompt 是 `Text Recognition:`、`Table Recognition:`、`Formula Recognition:`；資訊萃取必須嚴格遵守 JSON schema。舊版全文使用 `OCR`，表格與公式沒有產品入口。
7. **解析與後處理脆弱**：巢狀 JSON regex 深度有限，修正字典不遞迴處理 list/dict，多頁巢狀值以 `str()` 比較。
8. **PDF 記憶體尖峰**：舊版先把整份 PDF 所有頁面全部渲染並保留在 list；大檔案容易同時占用大量 RAM。

## 新架構

```text
React OCR UI
  → FastAPI /api/ocr/process
  → OCR task orchestration（逐頁、重試、後處理、SSE）
  → Provider abstraction
      ├─ local：Transformers in-process（預設）
      ├─ openai：vLLM / SGLang
      └─ ollama：舊環境相容

完整文件 task
  → 官方 glmocr PyPI SDK
  → PP-DocLayoutV3 版面分析（開啟 layout）
    或 WholePageLayoutDetector（關閉 layout，不載入版面模型）
  → InProcessSDKClient
  → 選定的 provider
  → Markdown + layout JSON（label/content/bbox）
```

### Provider 選擇

| Provider | 是否需要 Ollama | 使用情境 | 特性 |
|---|---:|---|---|
| `local` | 否 | 單機、離線、個人使用 | 模型直接載入 Backend；最少外部程序 |
| `openai` | 否 | 正式環境、多使用者、高吞吐 | 接 vLLM/SGLang；可平行批次與獨立管理 GPU |
| `ollama` | 是 | 舊部署相容 | 保留 `/api/generate`，但不再是預設或必要依賴 |

本機 provider 對 `model.generate()` 加鎖，避免同一 PyTorch 模型被多執行緒同時操作。這表示單程序本機模式重視穩定而不是吞吐；需要併發時應切換 vLLM/SGLang。

## 功能覆蓋

| GLM-OCR 能力 | 新版入口 | 輸出 |
|---|---|---|
| 完整文件解析 | `task=document` | Markdown + 每頁 layout JSON |
| PP-DocLayoutV3 | `enable_layout=true` | 版面 label、內容與座標 |
| 文字辨識 | `task=text` | Markdown/文字 |
| 表格辨識 | `task=table` | Markdown table/HTML（依模型回覆） |
| 公式辨識 | `task=formula` | LaTeX/Markdown |
| 資訊萃取 | `task=extract` + `fields` schema | 嚴格 JSON object |
| 圖片輸入 | OCR 頁面/API | PNG/JPG/JPEG/BMP/TIFF/WEBP/GIF |
| PDF 多頁 | OCR 頁面/API | 逐頁 SSE + 整份合併輸出 |
| Markdown 匯出 | OCR UI | `.md` |
| JSON 匯出 | OCR UI | `.json` |
| 自訂錯字修正 | 修正字典頁面/API | 巢狀 dict/list 遞迴處理 |
| GPU 記憶體釋放 | `POST /api/ocr/unload` | 卸載本機模型並清 CUDA cache |
| 能力探測 | `GET /api/ocr/capabilities` | provider、SDK、任務與功能狀態 |

## API 契約

### `POST /api/ocr/process`

`multipart/form-data`：

| 欄位 | 預設 | 說明 |
|---|---|---|
| `file` | 必填 | 圖片或 PDF，最大 50 MiB |
| `task` | `auto` | `document/text/table/formula/extract` |
| `fields` | `{}` | extract 使用的 JSON schema |
| `provider` | `local` | `local/openai/ollama` |
| `model` | `zai-org/GLM-OCR` | 模型 ID/服務模型名 |
| `max_retries` | `3` | 1–5 次，僅可重試錯誤進行退避重試 |
| `enable_layout` | `false` | document task 啟用 PP-DocLayoutV3 |
| `output_format` | `both` | `both/markdown/json` |
| `auto_merge` | `false` | 多頁 extract 欄位一致時合併 |
| `dpi` | `200` | 72–300 |

最後一個 SSE event 包含：

- `all_results`：逐頁結果。
- `document.markdown`：合併後 Markdown。
- `document.json`：各頁 JSON。
- `merged`：選用的多頁欄位合併結果。

## 安裝與設定

主 `requirements.txt` 會載入 `requirements-ocr.txt`：

```text
glmocr[selfhosted]==0.1.5
```

本次同步將 Torch/Transformers 對齊官方 SDK 0.1.5 的需求：Torch 2.10、TorchVision 0.25、Transformers 5.3 以上。ASR 已改用 Qwen 官方 `Qwen3-ASR-*-hf` 原生 checkpoint，不再安裝會鎖死 Transformers 4.57.6 的 `qwen-asr` 套件；因此 ASR 與 OCR 可由同一個 `.venv` 共用鎖定的 Hugging Face Transformers 官方 commit。Manager 的「重新安裝 Python 依賴」會使用這兩份 requirements。

預設不需要任何 OCR 環境變數。可選設定：

```env
OCR_PROVIDER=local
OCR_MODEL=zai-org/GLM-OCR
OCR_DEVICE=auto
GLMOCR_LAYOUT_DEVICE=

# 僅 openai provider
OCR_API_URL=http://127.0.0.1:8080/v1/chat/completions
OCR_API_KEY=
OCR_MAX_WORKERS=8

# 僅 ollama provider
OLLAMA_HOST=http://127.0.0.1:11434
```

本機辨識前需先由 Manager 下載 GLM-OCR 權重；啟用版面分析前另需下載 PP-DocLayoutV3。模型放在 Hugging Face 標準 cache，不進 Git，功能請求本身不會觸發下載。

## 效能與風險

1. **本機第一次執行較慢**：Manager 完成模型下載後，首次推論仍需建立 GPU graph/cache；後續請求沿用 singleton。
2. **完整 layout 比單頁直接辨識昂貴**：流程會先偵測區塊，再對文字、表格、公式區塊個別辨識；結果較完整但延遲較高。
3. **CPU 可執行但不適合大量文件**：0.9B BF16 模型雖小，CPU 延遲仍可能明顯。正式服務建議 vLLM/SGLang。
4. **單 GPU 與其他模型競爭**：專案同時有 ASR、CLIP、Semantic。OCR UI 提供釋放模型；CLIP 工作流也會先卸載 CLIP 再進 OCR。若仍發生 OOM，可把 `GLMOCR_LAYOUT_DEVICE=cpu` 或改用獨立推論服務。
5. **官方原生 ASR 仍需真實回歸**：pip dry-run 已證明 Qwen HF-native 與 glmocr 0.1.5 可共同解析，mock 也覆蓋兩套官方呼叫契約；但實際部署前仍應用真實 ASR/OCR 樣本做 GPU 準確率、速度與 VRAM 驗收。

## 驗證結果

已完成：

- Python `py_compile`：OCR provider、engine、router、workflow、CLIP 與測試檔全部通過。
- OCR tests：10 個不需 SDK/權重的測試全數通過，另 1 個條件式 SDK 整合測試在隔離安裝官方 PyPI `glmocr==0.1.5` 後通過。涵蓋本機 Transformers 官方 image-text 流程、官方 prompt、巢狀 JSON、遞迴修正、多頁衝突、SDK adapter、舊入口相容、SSE page contract，以及以假 provider 跑官方完整 document pipeline。
- React/Vite production build：57 modules transformed，建置成功。
- 原始碼搜尋：自有 OCR 路徑已不再引用 `backend/glm-ocr` 或舊 `backend/config.yaml`；Ollama 只存在於相容 provider 與其他獨立 LLM 功能。

尚未在本工作環境完成：

- 真實 GLM-OCR/PP-DocLayoutV3 權重推論。工作區原 `.venv` 指向已不存在的 Python 3.12，且目前沒有模型 cache；需要先由 Manager 重建環境並下載權重。
- GPU 速度、VRAM 與 OCR 準確率基準。這些數據與實際 GPU、CUDA、文件集高度相關，不應用 mock 測試代替。

建議驗收資料集至少包含：一般 A4 文字、跨頁 PDF、複雜表格、數學公式、直排文字、印章、手寫字、程式碼、掃描歪斜與低解析照片。每類應保存期望 Markdown/JSON，建立日後版本升級的固定回歸集。

## 官方依據

- GLM-OCR 官方倉庫與 SDK 安裝/部署說明：<https://github.com/zai-org/GLM-OCR>
- GLM-OCR 官方模型卡、Transformers 範例與 prompt 限制：<https://huggingface.co/zai-org/GLM-OCR>
- Transformers GLM-OCR 文件：<https://huggingface.co/docs/transformers/model_doc/glm_ocr>
- PyPI `glmocr` 0.1.5：<https://pypi.org/project/glmocr/>

官方模型卡明確區分兩類工作：文件解析建議使用含 PP-DocLayoutV3 的 SDK；資訊萃取則直接呼叫模型並嚴格遵守 JSON schema。本次架構即按此邊界實作。
