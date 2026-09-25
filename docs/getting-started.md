# 本機安裝與執行

需求：Git、uv、Python 3.10+、Node.js 22.12+ 與 npm。首次安裝需能存取 PyPI 與 npm registry。
本機開發不需要容器；FastAPI 同時提供 API 與建置後的前端。

## 首次啟動

在專案根目錄複製設定檔：

```sh
# macOS / Linux
cp .env.example .env
```

Windows PowerShell 改用 `Copy-Item .env.example .env`。
尚未設定 Gitea OAuth 時，在 `.env` 設定下列值，僅供本機開發：

```env
APP_ENV=development
AUTH_ENABLED=false
AUTH_COOKIE_SECURE=false
```

接著執行（PowerShell、macOS、Linux 皆可）：

```sh
uv sync
cd frontend
npm ci
npm run build
cd ..
uv run db-upgrade
uv run dev
```

`uv sync` 依 `uv.lock` 安裝相依，不必手動啟用 `.venv`。

- [操作介面](http://127.0.0.1:8000/)
- [Swagger API 文件](http://127.0.0.1:8000/docs)
- [健康檢查](http://127.0.0.1:8000/health)

全新安裝的專案清單為空；先依[操作手冊](user-guide.md)建立連線與專案。
關閉服務時在終端按 `Ctrl+C`。

## 日常開發

| 情境 | 指令 |
| --- | --- |
| 啟動後端 | `uv run dev`，預設 `127.0.0.1:8000`，Python 修改會自動重新載入 |
| 更換服務埠 | `uv run dev --port 8001`；OAuth callback 也需配合 |
| 修改前端 | 在 `frontend` 執行 `npm run build`，再重新整理網頁 |
| 使用前端開發伺服器 | 後端保持 8000，在 `frontend` 執行 `npm run dev`，網址以 Vite 輸出為準 |
| 更新資料庫 | `uv run db-upgrade`；用 `uv run db-current` 查看 revision |
| 修改 `.env` | 完整重啟後端 |
| 測試與品質檢查 | [品質檢查](quality-gates.md) |

Vite 開發伺服器將 `/api` 與 `/health` 代理到 `127.0.0.1:8000`。
若要測試完整 OAuth 登入，使用 FastAPI 提供的建置版頁面與一致的 callback URL。

## 設定與資料

- `.env.example` 的本機資料庫為 `sqlite:///./release.db`。
- `BACKGROUND_WORKER_ENABLED` 預設為 `false`；排程、背景追蹤與通知需開啟背景 worker。
- SMTP 留空時不寄信；通知設定見[更新與維運](operations.md)。
- `.env`、SQLite 檔案、`.venv`、`node_modules` 與 `app/static` 不提交至 Git。
- 測試使用暫存 SQLite 與模擬的外部服務，不需要正式 token。

正式環境設定另見[部署設定](deployment.md)，不要沿用本機停用驗證的設定。
