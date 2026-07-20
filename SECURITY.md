# 安全政策

## 支援範圍

安全修正以預設分支與最新 GitHub Release 為優先。較舊版本可能要求先升級後才能獲得修正；專案尚未承諾長期支援分支或固定修補時程。

## 私下通報漏洞

請使用 GitHub repository 的 **Security → Advisories → Report a vulnerability** 私下通報。若該功能不可用，請聯絡 repository owner，並避免在公開 Issue、Discussion、PR、log 或畫面截圖中揭露細節。

通報內容建議包含：

- 受影響的 commit、tag 與執行環境。
- 重現步驟、必要的最小測試資料與實際／預期結果。
- 可造成的影響、所需權限與是否能從網路觸發。
- 已知的暫時緩解方式。
- 若有 PoC，請移除真實 Token、Email、媒體與個人資料。

維護者會先確認收件，再進行重現、影響評估、修正與揭露協調。請在修補版本可供使用前保留合理的非公開期限。

## 部署安全基線

- 此專案預設適合本機或可信任 LAN，不應直接暴露在公網。
- 使用至少 32 字元、不可預測且每個部署唯一的 `SECRET_KEY`。
- SMTP 使用應用程式密碼或專用憑證，絕不提交 `.env`。
- 由反向代理提供 TLS、請求大小限制、速率限制、來源限制與安全 headers。
- 推論端點使用 JWT，但 JWT 放在 SSE／媒體 query string 時可能出現在瀏覽器歷史或 proxy log；公開部署應改為短效一次性 URL 或 cookie/header proxy。
- `/api/system/status`、`/health/*` 與 OpenAPI 文件會揭露有限的系統狀態；不需要時應由反向代理限制。
- Manager 預設可綁定 `0.0.0.0`。在未配置防火牆與 proxy 前，改綁 `127.0.0.1`。
- 定期更新 Python/npm 依賴並審核 Dependabot；模型 snapshot 也屬供應鏈輸入。
- `uploads/`、`results/`、`.manager/logs/` 與 `omni_ai.db` 可能含敏感內容，需設定 OS 權限、保存期限與備份政策。

## 已知架構限制

- Email OTP 尚未內建分散式速率限制；公網部署必須在 proxy/API gateway 層限制來源、信箱與端點頻率。
- JWT 預設有效 30 天，沒有 server-side revoke list。
- SQLite 與程序內工作執行器適用單機；多程序或多節點部署需外部資料庫、queue、鎖與一致的 Secret 管理。
- AI 產出可能不正確；涉及法律、醫療、財務、存取控制或不可逆決策時必須人工覆核。

只有本儲存庫程式碼採 MIT；上游模型、FFmpeg、套件與輸入資料仍受各自條款約束。
