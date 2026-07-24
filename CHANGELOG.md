# Changelog

本專案的重要變更記錄於此。格式參考 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本採 [Semantic Versioning](https://semver.org/)；正式版本與日期以 Git tag / GitHub Release 為準。

## [Unreleased]

## [1.1.8] - 2026-07-24

### Added

- `SMTP_SECURITY`（`auto`／`ssl`／`starttls`／`none`），讓自建 SMTP 可明確選擇加密方式。

### Changed

- SMTP 帳密改為選填；`SMTP_PORT` 可自訂；`SMTP_USER` 可為純使用者名稱。

### Fixed

- Email OTP 不再僅因 `SMTP_PORT=465` 強制 `SMTP_SSL`，避免自建伺服器出現 `SSL: WRONG_VERSION_NUMBER`。

[Unreleased]: https://github.com/zx90316/Omni-AI-GUI/compare/v1.1.8...HEAD
[1.1.8]: https://github.com/zx90316/Omni-AI-GUI/compare/v1.1.7...v1.1.8
