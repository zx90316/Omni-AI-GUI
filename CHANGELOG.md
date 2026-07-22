# Changelog

本專案的重要變更記錄於此。格式參考 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本採 [Semantic Versioning](https://semver.org/)；正式版本與日期以 Git tag / GitHub Release 為準。

## [Unreleased]

### Added

- ASR 媒體片段起訖選取與 sample-accurate WAV 裁切。
- 可重用的媒體範圍播放器與響應式前端互動。
- Backend liveness/readiness、Manager 程序採用、持久化 log 與完整 process-tree 終止測試。
- GitHub CI、Dependabot、Issue/PR templates 與專業維護文件。
- 專案生命週期、建置、架構、設定、開發、測試與發布文件。
- Nuitka standalone Manager 發行包、內容 allowlist、自我檢查、SHA-256、可選 Authenticode 簽章與 GitHub provenance attestation。

### Changed

- Manager 有 lockfile 時使用 `npm ci`，提升前端依賴可重現性。
- Transformers 鎖定已驗證的官方 Qwen3-ASR commit。
- Release 建置要求乾淨工作樹，不再自動 stage、commit 或 push 使用者檔案。
- README 重寫為完整的安裝、使用、建置、模型、安全與貢獻入口。
- Windows 發行流程完全改用 Nuitka；tag 或手動 workflow 會自動建立 GitHub Release。

### Fixed

- 支援 NVIDIA 610 系列驅動的 `KMD Version` 與 `CUDA UMD Version` 輸出，避免 RTX 5090 被誤判為 CPU 模式。
- Manager 鎖定相容的 ttkbootstrap 1.x，避免乾淨 CI 環境安裝 2.x 後造成 Nuitka 外部專案匯入驗證失敗。
- Windows 停止 Frontend 時會終止完整 npm/node/Vite process tree。
- Manager 環境設定群組在不同 Tk/ttkbootstrap widget 實作下的 padding 相容性。
- OCR 單元測試不再受開發機 `OCR_DEVICE` 影響。
- 上傳媒體片段選取的驗證、傳遞與轉檔契約。
- Manager executable 會自動找到同目錄下 clone 的 `Omni-AI-GUI/`，並記住上次有效的專案位置，避免重新啟動時重複詢問下載。
- Release 建置偵測到輸出目錄內已有安裝專案時會拒絕清理，避免重建誤刪使用者 clone。
- GitHub Release workflow 改用 PowerShell hashtable splatting，正確傳遞 Nuitka 版本、簽章與 skip-install 參數。

### Security

- OCR、CLIP、workflow 與 semantic 等高成本推論路由統一要求 JWT。
- Email OTP 改用密碼學安全亂數來源。
- `uploads/` 與更多 runtime/build artifact 納入 `.gitignore`。
- 發行包固定採不自解壓、不加殼的 Manager-only standalone，並驗證不含 Backend/Frontend source、secrets、資料庫、使用者資料或 cache。

[Unreleased]: https://github.com/zx90316/Omni-AI-GUI/compare/v1.1.0...HEAD
