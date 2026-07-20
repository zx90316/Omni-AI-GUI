# Changelog

本專案的重要變更記錄於此。格式參考 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本採 [Semantic Versioning](https://semver.org/)；正式版本與日期以 Git tag / GitHub Release 為準。

## [Unreleased]

### Added

- ASR 媒體片段起訖選取與 sample-accurate WAV 裁切。
- 可重用的媒體範圍播放器與響應式前端互動。
- Backend liveness/readiness、Manager 程序採用、持久化 log 與完整 process-tree 終止測試。
- GitHub CI、Dependabot、Issue/PR templates 與專業維護文件。
- 專案生命週期、建置、架構、設定、開發、測試與發布文件。

### Changed

- Manager 有 lockfile 時使用 `npm ci`，提升前端依賴可重現性。
- Transformers 鎖定已驗證的官方 Qwen3-ASR commit。
- Release 建置要求乾淨工作樹，不再自動 stage、commit 或 push 使用者檔案。
- README 重寫為完整的安裝、使用、建置、模型、安全與貢獻入口。

### Fixed

- Windows 停止 Frontend 時會終止完整 npm/node/Vite process tree。
- Manager 環境設定群組在不同 Tk/ttkbootstrap widget 實作下的 padding 相容性。
- OCR 單元測試不再受開發機 `OCR_DEVICE` 影響。
- 上傳媒體片段選取的驗證、傳遞與轉檔契約。

### Security

- OCR、CLIP、workflow 與 semantic 等高成本推論路由統一要求 JWT。
- Email OTP 改用密碼學安全亂數來源。
- `uploads/` 與更多 runtime/build artifact 納入 `.gitignore`。

[Unreleased]: https://github.com/zx90316/Omni-AI-GUI/compare/v1.1.0...HEAD
