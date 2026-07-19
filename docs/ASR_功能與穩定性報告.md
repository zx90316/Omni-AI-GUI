# ASR 功能與穩定性報告

日期：2026-07-19

## 1. 結論摘要

本專案的 ASR 功能鏈已具備實用產品所需的主要能力：本地檔案與 YouTube 輸入、格式統一、長音訊切段、Qwen3-ASR 轉錄、字詞時間戳、可選語者分離、繁體轉換、字幕分句、持久化、進度串流與匯出。

偶發失敗最可能的主因不是辨識模型本身，而是原本的任務執行方式：所有任務共用同一組 `converted.wav` / `chunk_*.wav`，背景執行緒又可同時載入 ASR 與 diarization 模型。兩個任務重疊時，會出現檔案互刪、讀到半成品或 GPU VRAM 競爭，完全符合「平常成功、偶爾失敗」的表現。

本次已完成針對性的低風險修正，包括工作目錄隔離、單程序運算鎖、片段重試、失敗資源清理、進度容錯、重啟恢復、SSE 資料庫 fallback、YouTube 下載隔離與長音訊記憶體最佳化。另依 Qwen 官方新模型卡，從舊 `qwen-asr==0.0.6` 封裝改為 Transformers-native `Qwen3-ASR-*-hf` 與 `Qwen3-ForcedAligner-0.6B-hf`。這使 ASR 和 GLM-OCR 能安全共用新版 Transformers，不再有 4.57.6 與 5.3+ 的硬衝突。

## 2. 現行功能設計

### 2.1 輸入與任務建立

- 本地 ASR：`POST /api/tasks` 上傳音訊或影片，建立 SQLite 任務後以 daemon thread 執行。
- YouTube / SubSync：可從網址下載音訊，或上傳本地媒體，再共用 `ASREngine` 執行辨識。
- 任務記錄包含模型、語言、繁體轉換、語者分離、進度、錯誤、時間戳字元、字幕句子與語者分段。

### 2.2 ASR pipeline

1. 透過 PyAV 將輸入轉為 16 kHz、mono、PCM WAV。
2. 透過 Transformers 原生 `AutoModelForMultimodalLM` 延遲載入 `Qwen3-ASR-*-hf`，再以 `AutoModelForTokenClassification` 載入官方 HF Forced Aligner；支援本地 cache 與離線錯誤提示。
3. 短音訊直接辨識；長音訊依靜音位置切成約 60 秒片段，最長約 180 秒。
4. 將各片段字元時間戳加回原始時間偏移。
5. 可選擇執行 pyannote 語者分離。
6. 將字元時間戳、標點與語者區段合併，產生字幕短句及語者歸組段落。
7. 可轉為臺灣繁體中文，並將結果寫入 SQLite。

### 2.3 結果使用

- 前端透過 SSE 顯示處理進度。
- 任務完成後可查看原始全文、字幕短句與語者歸組結果。
- 支援 TXT / SRT 匯出、重新分句、移除標點及後續 LLM 處理。

### 2.4 設計優點

- 重型 ML 套件延遲匯入，降低 API 啟動成本。
- 長音訊有切段與時間偏移修正，不受單次模型上下文限制。
- ASR 完成後先卸載模型，再載入 diarization，原本已有降低 VRAM 峰值的意圖。
- 字元時間戳和 diarization 原始資料有持久化，可不重跑模型重新分句。
- 離線模式會先檢查模型 cache，錯誤訊息比底層 Hugging Face 例外容易理解。

## 3. 發現的穩定性風險

| 優先級 | 風險 | 可能症狀 | 本次狀態 |
|---|---|---|---|
| P0 | 所有 ASR 任務共用 `results/work/converted.wav` 與 `chunk_*.wav` | 偶發找不到檔案、格式錯誤、片段內容錯置、轉錄中途失敗 | 已修正：每任務獨立暫存目錄 |
| P0 | 多個背景執行緒同時載入 Qwen 與 pyannote | CUDA OOM、forced aligner 失敗、程序不穩 | 已修正：完整 pipeline 單程序序列化 |
| P1 | 進度 callback 的 DB commit 例外會直接中止 ASR | SQLite 短暫鎖定時，模型其實正常但任務失敗 | 已修正：rollback、記錄警告，轉錄繼續 |
| P1 | diarization 推論例外時未保證釋放 pipeline | 首次失敗後 VRAM 殘留，後續任務更容易失敗 | 已修正：`finally` 清理與 CUDA cache 釋放 |
| P1 | 服務重啟後記憶體進度消失，DB 任務仍是 processing | 前端 SSE 永久等待，任務看似卡死 | 已修正：啟動時標記中斷，SSE 改用 DB fallback |
| P1 | 同一 YouTube 影片重複提交會共用下載檔 | 下載／轉檔互相覆寫或刪除 | 已修正：task-id 專屬下載目錄 |
| P2 | 所有 CUDA GPU 一律使用 bfloat16 | 不支援 BF16 的 GPU 在推論期失敗 | 已修正：BF16 capability 檢查，否則 FP16 |
| P2 | 長音訊切片前再次整段載入記憶體 | 長錄音 RAM 峰值過高，可能被系統終止 | 已改善：切片改為 seek/read；靜音偵測仍為整段讀取 |
| P2 | 單次暫時性推論錯誤立即讓整個任務失敗 | CUDA/decoder 短暫異常沒有恢復機會 | 已修正：每片段最多 2 次嘗試 |
| P2 | 音訊檔沒有音軌時只會出現索引錯誤；resampler 尾端未 flush | 錯誤難理解或尾端極短內容被截斷 | 已修正 |
| P2 | YouTube 流程沒有保存 `raw_text` | YouTube 任務的「原始 ASR」結果為空 | 已修正 |
| P3 | 背景錯誤只有字串，沒有 traceback | 偶發故障難以定位 | 已修正：後端 logger 保留 stack trace |

## 4. 本次程式碼最佳化

### `backend/asr_engine.py`

- 改用 Qwen 官方 Transformers-native checkpoint，不再匯入 `qwen_asr.Qwen3ASRModel`。
- 轉錄採 `apply_transcription_request` 與官方 `parsed` decode；時間戳採 `prepare_forced_aligner_inputs` 與 `decode_forced_alignment`。
- 增加自動語言偵測及 Forced Aligner 官方支援的 11 種語言選項。
- ASR 與對齊模型維持單例生命週期，卸載時同步清理 processor、模型與 CUDA cache。
- 新增程序內 ASR 全域鎖，排除模型與 GPU 併發競爭。
- 使用 `TemporaryDirectory` 為每次 run 建立獨立工作區，結束後自動清理。
- 新增 `_transcribe_with_retry`，失敗前清理 CUDA cache 並做一次重試。
- 長音訊片段改為 `SoundFile.seek/read`，不再第二次整段讀入 RAM。
- 片段檔以 `finally` 清理，失敗也不殘留。
- diarization 以 `finally` 將 pipeline 移回 CPU 並釋放 CUDA cache。
- 進度 callback 失敗不再影響核心辨識。
- GPU dtype 依 BF16 支援度選擇 BF16 或 FP16。
- 只有真正完成才回報完成；失敗不會先送出 100%。

### `backend/audio_utils.py`

- 對無音軌媒體提供明確錯誤。
- flush resampler 尾端樣本，避免輸出 WAV 尾端截短。

### 任務路由與資料庫

- 本地與 YouTube 進度 DB 寫入失敗時 rollback 並繼續 ASR。
- 失敗時記錄完整 traceback，並可靠更新 `failed` 與 `completed_at`。
- SSE 同時比較百分比、訊息與 done，不再漏掉相同百分比的新訊息。
- SSE 在記憶體資料不存在時查詢 DB，終態不再依賴程序記憶體。
- 程序啟動時將上一次遺留的 pending / processing 任務標記為中斷失敗。
- 終態持久化後移除進度記憶體快取，避免長期累積。
- YouTube 下載、結果保存與暫存清理補強。

## 5. 驗證結果

已完成：

- `py_compile`：ASR engine、audio utils、database、app、兩個任務 router 與新增測試皆通過。
- `tests/test_split.py` 已更新為目前的字元級 API；標點分句與無標點 50 字強制切分通過。
- 既有 `tests/test_merge.py`：語者分派、標點還原與語者歸組通過。
- 新增 `tests/test_asr_stability.py`：
  - 兩個 concurrent run 實際被序列化。
  - 每個 run 使用不同暫存目錄，完成後目錄被清除。
  - 進度 callback 例外不會中止 ASR。
  - 第一次片段推論失敗後會重試並成功。
  - HF-native 轉錄、parsed decode、Forced Aligner 輸入與時間戳 decode 契約正確。
- 完整 `pip install --dry-run --ignore-installed -r requirements.txt` 成功：解析為 Torch 2.10/cu128、官方 Transformers commit（`5.15.0.dev0`）與 `glmocr==0.1.5`，未再發生依賴衝突。
- `git diff --check`：通過，只有 Git 的 LF/CRLF 提示。

未完成：

- 真實 Qwen3-ASR、ForcedAligner、pyannote 與 GPU 端到端測試。
- 多小時音訊的 RAM / VRAM 壓力測試。
- 真實 YouTube 下載與網路中斷重試測試。

目前專案 `.venv/Scripts/python.exe` 仍存在，但其 launcher 指向已不存在的 `C:\Users\zx020\AppData\Local\Programs\Python\Python312\python.exe`；在重建虛擬環境前，無法用專案完整依賴執行上述整合測試。

Manager 現在會實際執行 `.venv` Python 檢查健康狀態，而非只判斷檔案是否存在；建立環境時優先選 Python 3.12，並可重建這類已失效環境。

## 6. 官方 HF-native 選型依據

- 舊版 PyPI `qwen-asr==0.0.6` 固定 `transformers==4.57.6`，與 `glmocr[selfhosted]==0.1.5` 所需的 Transformers 5.3+ 無法共同解析。
- 第三方 Transformers 5.x fork 的作者後續已警告辨識準確率顯著下降，因此不採用。
- Qwen 官方 `Qwen3-ASR-1.7B-hf` 模型卡提供原生 Transformers 轉錄、語言提示、batch、Forced Aligner 與 `torch.compile` 流程，權重與 API 都不再依賴 `qwen-asr` 套件。
- Qwen3-ASR 原生支援於 2026-06-26 合入 Transformers，但尚未包含在當時的穩定發行版；requirements 因此鎖定 Hugging Face 官方 commit `7ea2320c76117e6742364808a666ef6f2fb40a67`，兼顧官方實作與可重現性。

官方參考：

- <https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf>
- <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B-hf>
- <https://huggingface.co/docs/transformers/main/model_doc/qwen3_asr>

## 7. 尚存風險與建議順序

1. **先重建 `.venv` 並做真實壓力測試。** 建議至少測試短 WAV、空白音訊、無音軌 MP4、2 小時錄音、兩個同時提交的任務、開關 diarization，以及中途重啟後端。
2. **加入任務取消與 watchdog timeout。** 現在模型若底層永久卡住，沒有取消或逾時機制；全域鎖後的其他任務也會一起等待。
3. **把背景 daemon thread 升級成 durable worker queue。** 目前重啟只能把任務標失敗，不能續跑；若需正式長時間服務，建議採單一 GPU worker + 持久佇列。
4. **若未來開多個 Uvicorn workers，改用跨程序鎖。** 現在 manager 以單一 Uvicorn process 啟動，因此程序內鎖有效；多 worker 部署時需改成檔案鎖、Redis lock 或獨立 GPU worker。
5. **降低靜音分析的 RAM 使用量。** `split_audio_by_silence` 目前仍整段讀取音訊，可再改成 streaming blocks。
6. **加入結構化監控。** 建議每任務記錄 task id、stage、attempt、audio duration、device、dtype、模型載入秒數、推論秒數、峰值 VRAM 與錯誤類型，才能量化偶發失敗率。
7. **補上上傳限制與內容驗證。** 後端目前缺少明確的檔案大小上限與 MIME/副檔名白名單，超大或非媒體檔會把錯誤延後到背景任務。

## 8. 建議驗收標準

- 連續處理 50 個短音訊，任務狀態 100% 能進入 completed 或帶明確原因的 failed，不得永久 processing。
- 同時提交 5 個任務時，僅一個進入 GPU pipeline，其餘顯示等待，不出現共用暫存檔錯誤或 CUDA OOM。
- 2 小時 16 kHz mono 音訊的 Python RAM 峰值應保持在可接受範圍，且所有 chunk 時間戳單調遞增。
- 強制終止並重啟後端後，舊任務須在 10 秒內顯示「服務重啟，任務已中斷」，SSE 不得永久連線。
- 人為讓第一次片段推論失敗時，第二次成功可完成任務；連續兩次失敗則須保留 task id、stage 與 traceback。
