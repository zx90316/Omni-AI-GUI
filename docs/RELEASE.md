# 發布流程

## 版本政策

正式版本使用 `vMAJOR.MINOR.PATCH` Git tag。破壞性 API／設定變更增加 MAJOR；相容功能增加 MINOR；修正增加 PATCH。FastAPI metadata、Frontend package 與 Release tag 尚未共用單一版本來源，發布者必須在 checklist 明確同步或記錄差異。

## 發布前

1. 將使用者可見變更加入 [CHANGELOG.md](../CHANGELOG.md)。
2. 確認 `.env`、資料庫、媒體、模型、log、runtime 與 build artifact 未被追蹤。
3. 執行離線單元測試、Frontend build、`git diff --check`。
4. 依風險執行真實 GPU/model、YouTube、SMTP 與長音訊驗收。
5. Review 依賴與模型授權；處理 Dependabot／安全掃描結果。
6. 建立並審核 release commit，確保工作樹乾淨。

## Windows Manager artifact

在已登入 GitHub CLI 且 `.venv` 健康的 Windows 主機執行：

```powershell
.\release.bat
```

腳本會：

1. 驗證 Git、GitHub CLI、`.venv` 與乾淨工作樹。
2. 安裝 Manager build dependencies。
3. 使用 PyInstaller `onedir` 產生 `dist/Omni-AI-Manager/`。
4. 壓縮成 `dist/Omni-AI-Manager.zip`。
5. 以目前已審核的 `HEAD` 建立 GitHub Release 與 tag。

腳本刻意不執行 `git add`、commit 或 push。Release 不應替使用者決定哪些本機變更要發布。

## Artifact smoke test

- 在沒有開發 shell 環境變數的乾淨 Windows 帳號解壓。
- 啟動 EXE，驗證 project root 選擇／clone 流程。
- 驗證 Manager 開啟、環境狀態、設定 editor 與 log。
- 啟動 Backend/Frontend，檢查 health、訪客登入與一項不需外部服務的功能。
- 確認關閉 Manager 後沒有殘留 uvicorn、npm 或 node process。
- 掃描 zip 並記錄 SHA-256；建議附在 Release notes。

## 發布後

- 從 GitHub 重新下載 artifact，而非使用本機原檔重測。
- 確認 badge、tag、Release notes 與 Changelog link 正確。
- 建立下一個 `[Unreleased]` 區段。
- 監看 crash、安裝、模型相容與安全回報。

## 回退

Git tag 與公開 Release 不應重寫。若 artifact 或程式有問題，先將 Release 標示為 pre-release／撤下 artifact，再以新的 patch version 修正；安全事件依 [SECURITY.md](../SECURITY.md) 協調揭露。已下載模型與 runtime 資料不受 Git code rollback 自動管理，回退前要確認 schema、cache 與設定相容性。
