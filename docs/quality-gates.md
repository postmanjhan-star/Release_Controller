# 測試圍欄與品質閘門

這個專案把品質檢查分成快速靜態檢查、隔離測試、production build 與瀏覽器 E2E。任何一層失敗都應阻擋合併。

## Python gate

```powershell
uv sync
Set-Location frontend
npm ci
npm run build
Set-Location ..
uv run quality
```

- Ruff 檢查錯誤、未使用名稱、imports、bugbear 與 Python 3.10 modern syntax。
- Ruff formatter 確保 `app`、`tests`、`alembic` 格式一致。
- pytest 使用 temporary SQLite fixture，不碰正式資料庫。
- `tests/test_frontend.py` 會驗證實際的首頁與靜態資源，執行前必須先建置前端。
  `app/static` 是未納入版本控制的產物，乾淨 checkout 不會包含它；前端來源變更後也需重新建置。
- coverage 啟用 branch coverage，總門檻為 85%。
- Drone/Gitea client tests mock `httpx`，驗證成功 payload、timeout、連線與 HTTP 錯誤分類。

## Frontend gate

```powershell
Set-Location frontend
npm ci
npm run quality
```

`quality` 依序執行：

1. `tsc --noEmit`：TypeScript strict 型別檢查。
2. ESLint：React hooks、TypeScript 與一般程式錯誤。
3. Prettier check：防止未格式化程式進入 repository。
4. Vitest + React Testing Library：測試 React shell、共用 component 與 typed API client。
5. UI contract：保護 API paths、BPMN markers、navigation 與獨立 build selection。
6. Vite production build。
7. Production smoke：確認 FastAPI static index 引用的 assets 真的存在且包含必要 UI。

Vitest coverage 目前涵蓋 React shell 與 typed API layer，statements/functions/lines 門檻 85%、branches 80%。legacy controller 由 TypeScript、ESLint、contract 與 Playwright 保護，之後搬入 hooks 時要同步納入 unit coverage。

## Browser E2E

第一次執行先安裝瀏覽器：

```powershell
Set-Location frontend
npx playwright install chromium
npm run test:e2e
```

Playwright 會自動啟動 Vite，並 mock Drone/Gitea/API 回應，因此不需要 token，也不會建立或刪除 release。測試驗證：

- Frontend/Backend builds 能載入。
- runtime target 能寫入表單。
- combined release button 在選取完成後啟用。
- service health 顯示正常。
- history navigation 更新畫面和 `aria-current`，且不重新載入整頁。

## Drone

根目錄 `.drone.yml` 的 `quality` Docker pipeline 會在所有 push 與 pull request 執行上述
gate，透過 `depends_on` 明確指定 `frontend-quality → python-quality → browser-e2e`。
`frontend-quality` 的 `npm run quality` 會在共用 workspace 產生 `app/static`，Python container
才能驗證首頁與資源；不可將 Python 測試提前到前端建置之前。

`main` push 的 gate 通過後，才會接續由 Docker runner 透過 SSH 執行 `deploy` pipeline；
設定方式見[自動 CI/CD](cicd.md)。第一次 Drone build 完成後，Repository 管理者仍需到
Gitea branch protection，把 Drone 回報的 quality commit status 設為 required status check。

若 runner 無法下載 Python、Node 或 Playwright images，需由管理者設定 registry mirror／
網路代理，或將 `.drone.yml` 的 image 改成已同步到內部 registry 的對應版本。
