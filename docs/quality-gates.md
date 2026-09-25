# 品質檢查

命令定義以 [`devtools/cli.py`](../devtools/cli.py) 與
[`frontend/package.json`](../frontend/package.json) 為準。

## Python

首次執行需先建置前端，`tests/test_frontend.py` 會檢查實際的 `app/static` 資源：

```sh
uv sync
cd frontend
npm ci
npm run build
cd ..
uv run quality
```

- `uv run lint`：Ruff lint 與格式檢查，涵蓋 `app`、`tests`、`alembic`、`devtools`。
- `uv run test`：完整 pytest 與 coverage；可接測試路徑，例如 `uv run test tests/test_api.py`。
- `uv run quality`：上述靜態檢查與完整測試。

Coverage 啟用 branch coverage，總門檻為 **85%**，設定在 `pyproject.toml`。
測試使用暫存 SQLite 及模擬 Drone / Gitea，不操作正式資料。

## 前端

在 `frontend` 目錄執行 `npm run quality`，依序完成：

1. TypeScript 型別檢查、ESLint 與 Prettier check。
2. Vitest / React Testing Library 測試及 UI contract。
3. Vite build 與產物 smoke check。

Coverage 涵蓋 React、API、登入狀態與 controller 等 `src` 程式；排除測試檔、型別定義
`domain.ts` 與入口 `main.tsx`。目前整體門檻為 statements **75%**、branches **60%**、
functions **82%**、lines **80%**，以 `frontend/vite.config.ts` 為準。

## 瀏覽器 E2E

在 `frontend` 目錄執行：

```sh
npx playwright install chromium
npm run test:e2e
```

Playwright 會啟動 Vite 並模擬 API，驗證 build 選取、操作按鈕、頁籤與 URL navigation。
`npm run quality:ci` 包含前端 quality 與 E2E；失敗時的 trace、截圖位於 `test-results`。

## CI 與修改要求

Drone 的順序為 `frontend-quality → python-quality → browser-e2e`。
Push / pull request 會觸發 quality，但排除 `deploy` 分支；只有 `main` push 通過後才接續
Portainer 部署。詳細流程見 [CI/CD](cicd.md)。

依專案協作規範：文件變更也需執行 `uv run test`；Python 變更跑 `uv run quality`；
前端變更跑 `npm run quality`，操作流程變更另跑 E2E。完成前檢查 `git diff --check` 與
`git status --short`。測試數量與實際 coverage 以當次執行結果為準。
