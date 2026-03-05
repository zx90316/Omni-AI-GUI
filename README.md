# Qwen-ASR-GUI

基於 [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) 的全方位語音辨識與字幕製作工具，支援語者分離、繁體中文轉換、外部影片下載以及可視化的後台管理。

## ✨ 核心功能

- 🎙️ **語音辨識** — 提供 Qwen3 ASR 1.7B (高品質) / 0.6B (輕量) 模型選擇。
- 👥 **語者分離** — 整合 pyannote.audio，自動識別音訊中的多位說話者。
- 🔤 **繁體中文轉換** — 辨識結果自動轉換為臺灣慣用用語。
- 📊 **長音訊分段** — 透過靜音偵測自動切分，完美支援任意長度音訊。
- 💻 **跨平台支援** — 0.6B 輕量模型可在無 GPU 環境下純 CPU 運行。
- 📄 **匯出格式** — 支援匯出為 TXT 文字檔 / SRT 字幕檔。

## 🚀 全新進階功能

- 🖥️ **Manager 管理面板 (GUI)** — 現代化設定介面，可一鍵安裝依賴 (Python/Node.js/FFmpeg)、設定 `.env`、啟動/停止服務與一鍵更新版本，同時也為獨立可執行檔 (.exe) 提供自動 Clone 專案功能。
- 🌐 **Web 前端介面 (React + Vite)** — 優雅且響應式的網頁介面，提供更進階的操作體驗。
- 🔄 **SubSync 字幕同步與處理** — 支援動態重新分段字幕、標點符號一鍵移除（可選擇替換為空白），大幅提升字幕閱讀性。
- 🎥 **YouTube 解析與下載** — 內建 YouTube 影片下載功能，可直接貼上網址並送入處理列表。
- 📋 **統一任務管理** — 整合本機檔案與 YouTube 下載任務，單一列表輕鬆掌握全部處理進度。

## 📋 安裝與啟動

### 方式一：下載可執行檔（最簡單）

前往 [GitHub Releases](https://github.com/zx90316/Qwen-ASR-GUI/releases) 下載最新的 `Qwen-ASR-Manager`，解壓縮後雙擊執行exe檔。程式會自動偵測環境，若不在專案目錄中將引導你自動 `git clone` 整個專案。

### 方式二：從原始碼啟動

```bash
python launch.py
```
> **提示**：`launch.py` 會自動安裝管理面板所需的基礎套件，透過 Manager 介面可一鍵建立 `.venv` 虛擬環境、執行 `pip install`、`npm install` 以及自動下載配置 FFmpeg。

### 環境變數設定
語者分離功能需要使用 Pyannote，請透過 Manager 面板或手動新增 `.env` 檔案並填入你的 [HuggingFace Token](https://huggingface.co/settings/tokens)：

```env
HF_TOKEN=hf_your_token_here
```

### 使用 Web 介面
在 Manager 面板中點擊「啟動 Backend」與「啟動 Frontend」，接著點選「🌐 開啟前端頁面」即可透過瀏覽器使用完整功能。

## 🖥️ 系統需求

| 模式 | 模型 | VRAM / RAM |
|------|------|-----------| 
| GPU | 1.7B (高品質) | 12 GB VRAM |
| GPU | 0.6B (輕量) | 10 GB VRAM |
| CPU | 0.6B (輕量) | 10 GB RAM |

*建議採用具有 CUDA 加速的 Nvidia GPU 以獲得最佳轉換速度。*

## 📁 主要專案結構

```
├── launch.py              # Manager 管理面板啟動入口（也是 .exe 打包來源）
├── manager/               # Manager 管理面板核心模組
├── frontend/              # React + Vite 前端網頁原始碼
├── backend/               # FastAPI 後端 API 服務
│   ├── app.py             # FastAPI 應用入口
│   ├── config.py          # 全域配置管理
│   ├── asr_engine.py      # ASR 核心語音引擎
│   ├── audio_utils.py     # 音訊處理工具
│   ├── database.py        # 資料庫模型與操作
│   └── routers/           # API 路由 (任務/YouTube/LLM/認證)
├── requirements.txt       # Python 依賴清單
├── .env.example           # 環境變數範例檔
└── qwen_asr.db            # SQLite 工作任務資料庫
```

## 📜 授權

MIT License
