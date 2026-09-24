# 本機安裝與執行

本機不需要 Docker 或 Podman。前端會先由 Vite 編譯到 `app/static`，再由同一個
FastAPI process 提供網頁與 API。

## 需求

- Git。
- uv。
- Python 3.10 或更新版本。
- Node.js 20.19+ 或 22.12+，以及 npm（Vite 8 的 engine 限制）。
- 可連線到 PyPI 與 npm registry（第一次安裝依賴時）。

確認版本：

```powershell
python --version
uv --version
node --version
npm --version
```

## Windows PowerShell 首次安裝

在 repository 根目錄執行：

```powershell
Copy-Item .env.example .env
uv sync

Set-Location frontend
npm ci
npm run build
Set-Location ..

uv run db-upgrade
uv run dev
```

`uv sync` 會依 `uv.lock` 建立或更新 `.venv`，不需執行 `Activate.ps1`；後續指令都透過
`uv run` 使用同一套鎖定的相依版本。

## Linux/macOS 首次安裝

```bash
cp .env.example .env
uv sync

cd frontend
npm ci
npm run build
cd ..

uv run db-upgrade
uv run dev
```

## 後端 uv 指令

以下指令在 Windows、Linux 與 macOS 相同，並會在需要時自動同步 `.venv`：

| 指令 | 用途 |
| --- | --- |
| `uv sync` | 依 `uv.lock` 安裝正式與開發相依 |
| `uv run dev` | 以 reload 模式啟動 `127.0.0.1:8000` |
| `uv run db-upgrade` | 將本機資料庫 migration 升到 head |
| `uv run db-current` | 顯示目前 migration revision |
| `uv run lint` | 執行 Ruff lint 與 format check |
| `uv run test` | 執行 pytest 與 85% coverage gate |
| `uv run quality` | 依序執行 lint、format check 與測試 |

`dev` 與 `test` 可接續原工具參數，例如 `uv run dev --port 8001` 或
`uv run test tests/test_api.py`。

## 啟動確認

開啟以下網址：

- 前端：<http://127.0.0.1:8000/>
- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>

PowerShell 健康檢查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/gitea/health
```

預期內容：

```json
{
  "status": "ok",
  "service": "release-controller",
  "database": "ok"
}
```

終止服務時，在執行 Uvicorn 的終端按 `Ctrl+C`。

## 日常開發

只修改 Python 時，`--reload` 會重新載入，不需重建前端。修改 `frontend/src`
後需重新執行：

```powershell
Set-Location frontend
npm run build
Set-Location ..
```

再重新整理瀏覽器。正式由 FastAPI 提供的是 `app/static`，不是 Vite dev server。

新增 migration 後或拉到含新 migration 的版本時：

```powershell
uv run db-upgrade
uv run db-current
```

## 設定

`.env.example` 可複製成不進版控的 `.env`：

| 變數 | 本機預設 | 說明 |
| --- | --- | --- |
| `APP_NAME` | `release-controller` | 健康檢查及 FastAPI 名稱 |
| `APP_ENV` | `development` | 環境標記，啟動時寫入 log |
| `LOG_LEVEL` | `INFO` | Python log level |
| `DATABASE_URL` | `sqlite:///./release.db` | 本機 SQLite 檔案 |

設定在 process 啟動時讀取。修改 `.env` 後請完整重啟 Uvicorn。

## 測試

```powershell
uv run lint
uv run test

# 或一次執行完整 Python gate
uv run quality

Set-Location frontend
npm ci
npm run quality
npx playwright install chromium
npm run test:e2e
Set-Location ..
```

上述 pytest 使用隔離的暫存 SQLite，不會讀寫本機 `release.db` 或正式
`/data/release.db`。Python branch coverage 不得低於 85%；React/API typed layer 的
statements、functions、lines 不得低於 85%，branches 不得低於 80%。

`frontend/tests/ui-smoke.cjs` 驗證 production HTML 引用的 JS/CSS 真的存在；
`frontend/tests/e2e/` 使用 route mocks 在 Chromium 驗證 build 載入、按鈕狀態、頁籤與
URL navigation，不會呼叫正式 Drone、Gitea 或修改資料庫。失敗 trace 與截圖輸出到
`test-results`（已由 Git 忽略）。

## 本機資料

`release.db`、WAL/SHM 檔、`.env`、`.venv` 與 `frontend/node_modules` 都已由
`.gitignore` 排除。不要提交真實 token、使用者資料或正式資料庫。
