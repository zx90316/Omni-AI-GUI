# ASR 功能與穩定性報告

日期：2026-07-19

## 1. 結論摘要

本專案的 ASR 功能鏈已具備實用產品所需的主要能力：本地檔案與 YouTube 輸入、格式統一、長音訊切段、Qwen3-ASR 轉錄、字詞時間戳、可選語者分離、繁體轉換、字幕分句、持久化、進度串流與匯出。

偶發失敗最可能的主因不是辨識模型本身，而是原本的任務執行方式：所有任務共用同一組 `converted.wav` / `chunk_*.wav`，背景執行緒又可同時載入 ASR 與 diarization 模型。兩個任務重疊時，會出現檔案互刪、讀到半成品或 GPU VRAM 競爭，完全符合「平常成功、偶爾失敗」的表現。

本次已完成針對性的低風險修正，包括工作目錄隔離、單程序運算鎖、片段重試、失敗資源清理、進度容錯、重啟恢復、SSE 資料庫 fallback、YouTube 下載隔離、長音訊串流分析、合作式取消、全流程逾時、串流上傳限制與對齊降級。另依 Qwen 官方新模型卡，從舊 `qwen-asr==0.0.6` 封裝改為 Transformers-native `Qwen3-ASR-*-hf` 與 `Qwen3-ForcedAligner-0.6B-hf`。這使 ASR 和 GLM-OCR 能安全共用新版 Transformers，不再有 4.57.6 與 5.3+ 的硬衝突。

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
| P2 | 長音訊靜音分析與切片載入整段音訊 | 長錄音 RAM 峰值過高，可能被系統終止 | 已修正：靜音分析採固定大小串流 block，切片採 seek/read |
| P2 | 單次暫時性推論錯誤立即讓整個任務失敗 | CUDA/decoder 短暫異常沒有恢復機會 | 已修正：每片段最多 2 次嘗試 |
| P2 | 音訊檔沒有音軌時只會出現索引錯誤；resampler 尾端未 flush | 錯誤難理解或尾端極短內容被截斷 | 已修正 |
| P2 | YouTube 流程沒有保存 `raw_text` | YouTube 任務的「原始 ASR」結果為空 | 已修正 |
| P2 | 任務排隊或生成階段無法取消、無總逾時 | 任務永久 processing，後續任務一起等待 | 已修正：共享取消事件、鎖等待輪詢、生成停止條件與四小時預設逾時 |
| P2 | 上傳沒有大小與副檔名限制 | 超大或非媒體資料占滿磁碟，錯誤延後到背景工作 | 已修正：白名單、2 GiB 預設上限、串流寫入與半成品清理 |
| P2 | Forced Aligner 單點失敗會丟棄已完成轉錄 | 有文字但整個任務仍失敗 | 已改善：保留轉錄、產生近似時間戳並在 UI 明確警告 |
| P2 | 受保護的本機媒體 URL 未帶登入權杖 | 任務完成但 `<video>` 播放回傳 401 | 已修正：媒體 URL 帶入 URL-encoded token，後端仍驗證擁有者 |
| P1 | Transformers 對 Windows 檔案路徑改用 TorchCodec | libtorchcodec / FFmpeg ABI 不相容，模型載入成功但每次轉錄都失敗 | 已修正：以 soundfile 解碼 16 kHz waveform，ASR 與 aligner 共用 NumPy 音訊 |
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
- 全域鎖改為輪詢取得，排隊期間也能取消或逾時。
- Transformers generation 加入停止條件，避免只在片段完成後才響應取消。
- 靜音偵測改為約 10 秒的 `SoundFile.blocks` 串流與 NumPy 向量化 RMS。
- Forced Aligner 失敗時產生可用的近似時間戳，並把降級狀態寫入結果警告。
- 轉錄輸入改為 `soundfile` 解碼後的 contiguous float32 waveform，避開 Windows TorchCodec native DLL，且 ASR / aligner 不重複解碼。

### `backend/audio_utils.py`

- 對無音軌媒體提供明確錯誤。
- flush resampler 尾端樣本，避免輸出 WAV 尾端截短。
- 解碼每個 frame 時檢查取消事件，長影片轉檔不再無法中止。

### 任務路由與資料庫

- 本地與 YouTube 進度 DB 寫入失敗時 rollback 並繼續 ASR。
- 失敗時記錄完整 traceback，並可靠更新 `failed` 與 `completed_at`。
- SSE 同時比較百分比、訊息與 done，不再漏掉相同百分比的新訊息。
- SSE 在記憶體資料不存在時查詢 DB，終態不再依賴程序記憶體。
- 程序啟動時將上一次遺留的 pending / processing 任務標記為中斷失敗。
- 終態持久化後移除進度記憶體快取，避免長期累積。
- YouTube 下載、結果保存與暫存清理補強。
- 本地與 YouTube 任務新增 idempotent cancel API、`cancelling` / `cancelled` 終態，以及重啟後的取消狀態恢復。
- 上傳改為分塊寫入，限制副檔名、空檔與總大小；任何錯誤都移除半成品。
- YouTube 下載加入 socket timeout、重試、fragment retry 與下載進度取消檢查。
- `/api/system/status` 回報 ASR busy、受控任務數、逾時與上傳限制，便於現場診斷。
- 活躍任務禁止直接刪除；媒體取得同時驗證任務擁有者，避免跨使用者讀取。
- Manager 模型下載進度在 snapshot 驗證前最多顯示 99%，避免 Windows 下載／重建 byte 雙計數造成假完成。
- 模型 cache 以完整 snapshot 權重及 sharded index 判定可用性，不再被未引用的 stale `.incomplete` 暫存檔誤判。

## 5. 驗證結果

已完成：

- `py_compile`：ASR engine、audio utils、database、app、兩個任務 router 與新增測試皆通過。
- `tests/test_split.py` 已更新為目前的字元級 API；標點分句與無標點 50 字強制切分通過。
- 既有 `tests/test_merge.py`：語者分派、標點還原與語者歸組通過。
- `tests/test_asr_stability.py` 與 `tests/test_asr_api.py` 共 14 項全部通過：
  - 兩個 concurrent run 實際被序列化。
  - 每個 run 使用不同暫存目錄，完成後目錄被清除。
  - 進度 callback 例外不會中止 ASR。
  - 第一次片段推論失敗後會重試並成功。
  - HF-native 轉錄、parsed decode、Forced Aligner 輸入與時間戳 decode 契約正確。
  - 鎖等待中的取消與逾時會正確結束。
  - 取消 API 可重複呼叫，活躍任務不可直接刪除。
  - 非支援、空白與超限上傳都會拒絕且不殘留檔案。
  - 媒體端點會阻止其他使用者存取。
  - 130 秒 WAV 的靜音分析採串流處理並找到正確邊界。
  - 對齊降級仍保留文字 token 與完整音訊時間範圍。
- 完整 `pip install --dry-run --ignore-installed -r requirements.txt` 成功：解析為 Torch 2.10/cu128、官方 Transformers commit（`5.15.0.dev0`）與 `glmocr==0.1.5`，未再發生依賴衝突。
- Manager lifecycle 16 項、模型管理 17 項、OCR 11 項單元測試全部通過。
- 完整 `unittest discover` 共 58 項測試全部通過；FastAPI 啟停已遷移至 lifespan，Pydantic schema example 也已改為 v2 相容寫法，消除既有框架棄用警告。
- Vite production build 通過（57 modules transformed）。
- 實際啟動 Uvicorn 後，`/health/live`、`/health/ready`、`/api/system/status` 均回傳 200；ASR 狀態為 idle、0 tracked tasks、4 小時 timeout、2 GiB upload limit。
- 官方 `Qwen3-ASR-1.7B-hf`、`Qwen3-ASR-0.6B-hf`、`Qwen3-ForcedAligner-0.6B-hf` 與 gated `speaker-diarization-community-1` 已下載並通過本機離線 snapshot 驗證。
- 真實 RTX 3090 CUDA 端到端測試通過：11.04 秒臺灣中文合成語音，33.61 秒內完成（含模型冷載入），產生 34 個字元時間戳與 2 個字幕段，時間範圍 0.08–10.32 秒，無降級警告。
- 預設 1.7B 模型以同一音訊完成真實 CUDA 測試：40.95 秒（含模型冷載入），同樣產生 34 個時間戳、2 個字幕段與 0.08–10.32 秒範圍，無降級警告。
- 真實 pyannote CUDA 測試通過：32.24 秒完成，辨識兩個語音區段並正確歸於同一位 `SPEAKER_00`。pyannote 雖警告 TorchCodec 不可用，但因 pipeline 接收預載 waveform，未影響推論。
- 2 小時串流分析壓測通過：219.73 MiB、7200 秒 WAV 在 0.72 秒完成靜音掃描，產生 40 個合法片段且末端精確到 7200 秒，Python RSS 峰值僅增加 2.95 MiB。
- 真實 YouTube 全鏈路通過：公開 19.01 秒影片由 yt-dlp 下載並透過 bundled FFmpeg 產生 3.65 MB WAV；0.6B ASR 於 24.58 秒完成英文轉錄，產生 4 個字幕段與 37 個對齊 token，無降級警告。
- 真實 Transformers generation 取消測試通過：83.18 秒音訊在第二片段生成時觸發取消，48.29 秒結束並拋出帶階段資訊的 `ASRCancelledError`，未殘留任何 `results/work/asr_*` 工作目錄。
- `git diff --check`：通過，只有 Git 的 LF/CRLF 提示。

尚未執行：

- 多小時「真實語音＋模型推論」的 GPU 時間與 VRAM 壓力測試（2 小時串流切片的 RAM 測試已完成）。
- 人工注入下載中途斷線後的 yt-dlp 三次重試測試（真實正常網路下載與取消前置流程已驗證）。

原本 `.venv/Scripts/python.exe` 的 launcher 指向已被移除的系統 Python。官方安裝器因 Windows policy 1625 被拒後，已改用 Python.org 官方 3.12.10 embeddable runtime（MD5 與 Python Software Foundation 簽章均驗證）作為專案本機 base runtime；目前 Manager 判定 `.venv` 健康，FastAPI、Torch 2.10.0+cu128、CUDA 與 Transformers 5.15.0.dev0 均可直接載入。`.python-runtime/` 屬機器本機資產並已加入 Git ignore。

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

1. **執行長音訊模型推論與多人語音壓力測試。** `.venv`、1.7B / 0.6B ASR、aligner 與 pyannote 現已可用，2 小時切片 RAM 壓測也已通過；仍建議以真實 2 小時語音測量 GPU 總時間與峰值 VRAM，並測試多人語音、開關 diarization、兩個同時提交的任務，以及中途重啟後端。
2. **把背景 daemon thread 升級成 durable worker queue。** 目前重啟會把中斷任務轉為明確終態，但不能續跑；正式長時間服務建議採單一 GPU worker + 持久佇列。
3. **若未來開多個 Uvicorn workers，改用跨程序鎖。** 現在 Manager 以單一 Uvicorn process 啟動，因此程序內鎖有效；多 worker 部署時需改成檔案鎖、Redis lock 或獨立 GPU worker。
4. **加入結構化監控。** 建議每任務記錄 task id、stage、attempt、audio duration、device、dtype、模型載入秒數、推論秒數、峰值 VRAM、是否使用近似時間戳與錯誤類型，才能量化偶發失敗率。
5. **評估更強的內容型別檢查。** 現在已有副檔名白名單與 PyAV 解碼驗證；若部署在公開網路，可再於寫入前檢查 magic bytes 或使用惡意內容掃描服務。

## 8. 建議驗收標準

- 連續處理 50 個短音訊，任務狀態 100% 能進入 completed 或帶明確原因的 failed，不得永久 processing。
- 同時提交 5 個任務時，僅一個進入 GPU pipeline，其餘顯示等待，不出現共用暫存檔錯誤或 CUDA OOM。
- 2 小時 16 kHz mono 音訊的 Python RAM 峰值應保持在可接受範圍，且所有 chunk 時間戳單調遞增。
- 強制終止並重啟後端後，舊任務須在 10 秒內顯示「服務重啟，任務已中斷」，SSE 不得永久連線。
- 人為讓第一次片段推論失敗時，第二次成功可完成任務；連續兩次失敗則須保留 task id、stage 與 traceback。
