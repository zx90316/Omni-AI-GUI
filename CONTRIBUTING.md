# 貢獻指南

感謝你協助改善 Omni AI Manager。專案同時包含桌面程序管理、Web API、GPU 模型推論與前端互動；請讓每次變更保持可審核、可回退並有對應驗證。

## 開始之前

- 一般錯誤與功能提案請使用 GitHub Issue template。
- 安全漏洞不要建立公開 Issue，請依 [SECURITY.md](SECURITY.md) 私下通報。
- 大型架構改動、依賴替換或資料庫 schema 變更，建議先建立討論 Issue。
- 請確認第三方程式、模型或資料可依法納入 MIT 專案，並保留必要 attribution。

## 開發環境

建議使用 Windows、Python 3.12、Node.js 20 以上與 Git。完整安裝方式見 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

```powershell
git clone https://github.com/zx90316/Omni-AI-GUI.git
Set-Location Omni-AI-GUI
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Set-Location frontend
npm.cmd ci
```

## 分支與提交

- 從最新預設分支建立短期 feature branch。
- 一個 Pull Request 聚焦一個可清楚描述的目的。
- Commit message 建議採 `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `build:`, `ci:` 或 `chore:` 前綴。
- 不要混入格式化整個儲存庫、模型權重、產生檔或個人設定。

## 程式碼原則

- Backend 推論保持 local-only；模型下載只能由 Manager 的明確操作觸發。
- 長時間任務必須考慮取消、逾時、重啟後終態與暫存清理。
- GPU 模型需避免無界併發、重複載入與未釋放的 CUDA cache。
- 檔案上傳需限制類型與大小，使用安全檔名並確保失敗時清除半成品。
- 新推論 API 預設必須驗證；若需要公開端點，PR 中要說明風險與理由。
- Manager UI 的耗時操作不得阻塞 Tk main thread。
- 前端互動要保留鍵盤操作、可見 focus、label 與錯誤狀態。
- 不要在 log、錯誤訊息或測試 fixture 中寫入 Token、密碼、OTP 或個人資料。

## 驗證

至少執行與變更範圍相符的檢查：

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend manager tests launch.py
.\.venv\Scripts\python.exe -m unittest -v `
  tests.test_manager_lifecycle `
  tests.test_model_management `
  tests.test_semantic_engine `
  tests.test_glmocr `
  tests.test_asr_stability `
  tests.test_asr_api

Set-Location frontend
npm.cmd run build
```

涉及真實模型、CUDA、YouTube、SMTP 或大型檔案時，請另外列出硬體、模型、輸入長度、耗時與結果；不要把測試媒體提交到儲存庫。

## Pull Request checklist

- [ ] 說明問題、解法與不在本次範圍內的項目。
- [ ] 已加入或更新回歸測試。
- [ ] 已執行適當的 Python 與 Frontend 檢查。
- [ ] 已更新 README、設定、API、架構或 Changelog 文件。
- [ ] 沒有提交 Secret、`.env`、資料庫、媒體、模型、log 或 build artifact。
- [ ] 沒有不必要的 lockfile 或整檔格式變動。
- [ ] 對相容性、資源使用與安全影響有明確說明。

送出貢獻即表示你同意依專案的 [MIT License](LICENSE) 提供該變更。
