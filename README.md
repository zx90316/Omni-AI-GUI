# Omni AI

全方位語音辨識與多模態 AI 處理工具，支援高精度語音辨識、語者分離、影片下載、圖片搜尋、OCR 文字擷取、語意分析，並提供現代化的 Manager 與 Web 前端管理介面。

## ✨ 核心大模型功能

- 🎙️ **語音辨識 (ASR)** — 內建 Qwen3 ASR 1.7B (高品質) / 0.6B (輕量) 模型，支援自動轉換臺灣慣用繁體中文。
- 👥 **語者分離** — 整合 pyannote.audio，自動識別音訊中的多位說話者。
- � **圖片搜尋 (ClipSearch)** — 基於視覺模型 (CLIP) 的語意圖片檢索系統。
- � **OCR 智慧擷取** — 支援 PDF 與圖片之光學字元辨識，整合 Ollama `glm-ocr` 模型進行結構化欄位擷取。
- 🧠 **語意引擎 (Semantic)** — 支援文本 Embedding 與 Reranking，為檢索系統增強精確度。
- ⚙️ **整合工作流 (Workflow)** — 將多個 AI 模組串聯（例：ClipSearch 檢索影像後，透過 OCR 自動提取關鍵欄位）。

## 🚀 系統與進階功能

- 🖥️ **Manager 管理面板 (GUI)** — 現代化設定介面，提供一鍵安裝依賴、啟動/停止服務與一鍵版本更新。
- 📦 **單一執行檔與自動部屬** — 提供打包好的 `.exe` 檔，支援首次執行自動 `git clone` 專案與環境建置。
- 🌐 **Web 前端介面 (React + Vite)** — 優雅且響應式的網頁介面，提供視覺化任務列表與多模組操作。
- 🎥 **YouTube 解析與下載** — 內建 YT 影片下載功能，可直接貼上網址並送入排程。
- � **SubSync 字幕優化** — 支援動態重新分段與標點符號一鍵移除，提升字幕閱讀性。
- �📋 **統一任務管理** — 單一列表監控所有本機檔案、下載與 AI 處理任務的系統。

## 📋 安裝與啟動

### 方式一：下載可執行檔（最簡單推薦）

前往 [GitHub Releases](https://github.com/zx90316/Omni-AI-GUI/releases) 下載最新的 `Omni-AI-Manager`，解壓縮後雙擊執行 `.exe` 檔。程式會自動偵測環境，若不在專案目錄中將引導你自動下載專案原始碼，並自動配置 Python 虛擬環境 (`.venv`) 與安裝依賴套件。

### 方式二：從原始碼啟動

```bash
python launch.py
```
> **提示**：系統將透過 `launch.py` 啟動前端 Manager 面板。你可於介面中一鍵建立 `.venv` 虛擬環境、執行 `pip install`、`npm install` 並自動下載設定 FFmpeg。

### 🔑 環境變數與模型設定

- **Pyannote (語者分離)**：於 Manager 介面設定或新增 `.env` 檔案，填入 [HuggingFace Token](https://huggingface.co/settings/tokens)：
  ```env
  HF_TOKEN=hf_your_token_here
  ```
- **Ollama/OCR**：確保本機已安裝 Ollama 並部署 `glm-ocr` 來啟用 OCR 欄位擷取功能。
- **本地模型存放**：Embedding/Reranking 模型在初次使用時會自動下載至預設模型目錄（由 Semantic Engine 管理）。

### 🌐 使用 Web 介面
於 Manager 面板依序點擊「啟動 Backend」與「啟動 Frontend」，接著按「🌐 開啟前端頁面」即可在瀏覽器使用完整服務介面。

## 🖥️ 效能與系統需求 (參考)

| AI 功能 | 模型/服務 | VRAM / RAM |
|------|------|-----------| 
| ASR (GPU) | Qwen3 1.7B | 12 GB VRAM |
| ASR (GPU) | Qwen3 0.6B | 10 GB VRAM |
| ASR (CPU) | Qwen3 0.6B | 10 GB RAM |
| OCR | Ollama `glm-ocr` | 具備基礎系統資源即可 |
| Semantic | BGE 等語意模型 | 具備基礎系統資源即可 |

*提示：ASR 語音辨識功能建議採用具備 CUDA 加速的 Nvidia GPU 以獲得最佳轉換速度。*

## 📁 主要專案結構

```
├── launch.py              # Manager 管理面板啟動入口（與打包來源）
├── manager/               # Manager 管理面板核心模組
├── frontend/              # React + Vite 前端網頁原始碼
├── backend/               # FastAPI 後端 API 服務
│   ├── app.py             # FastAPI 應用入口
│   ├── routers/           # API 路由 (asr, clip_search, ocr, semantic, tasks, workflow, youtube 等)
│   ├── *_engine.py        # 核心 AI 推理引擎 (asr_engine, clip_engine, ocr_engine, semantic_engine 等)
│   ├── config.py          # 全域配置管理
│   ├── database.py        # 資料庫模型與操作
│   └── audio_utils.py     # 音訊處理工具
├── requirements.txt       # Python 依賴清單
├── .env.example           # 環境變數範例檔
└── omni_ai.db             # SQLite 工作任務與資料庫
```

## 📜 授權

MIT License
