# Release Controller

整合 **Gitea 與 Drone** 的多專案發布管理工具，集中處理版本選擇、部署、發布與稽核紀錄。
後端使用 FastAPI、SQLAlchemy 與 SQLite，前端使用 React + TypeScript。

## 主要功能

- **專案與連線管理**：設定各專案的元件、repository、部署順序與 target，連線 token 加密保存。
- **部署與發布**：各元件獨立選擇 Drone build，支援單一元件或循序 Bundle 部署，再建立 Gitea Release。
- **流程追蹤**：提供核准流程、BPMN 狀態圖與事件歷程，分別追蹤部署與發布結果。
- **排程與通知**：支援時區、背景執行、SMTP 通知與附件。
- **登入驗證**：使用 Gitea OAuth2 + PKCE，可限制專案與連線僅由 Gitea 管理員修改。

## 本機快速啟動

需求：uv、Python 3.10+、Node.js 22.12+ 與 npm。

在專案根目錄將 `.env.example` 複製為 `.env`。尚未設定 Gitea OAuth 時，本機開發可使用：

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

啟動後開啟 [操作介面](http://127.0.0.1:8000/)，先在 **Projects → Connections** 建立 Drone / Gitea
連線，再到 **Projects** 設定元件與部署目標，即可選擇 build 進行部署。
另提供 [API 文件](http://127.0.0.1:8000/docs)與[健康檢查](http://127.0.0.1:8000/health)。

Repository、元件順序與 target 由 UI 管理。使用排程與背景追蹤時，須設定
`BACKGROUND_WORKER_ENABLED=true`；寄送通知另需 SMTP 設定。SQLite 部署僅啟動一個背景 worker。

## 常用開發指令

| 指令 | 用途 |
| --- | --- |
| `uv run dev` | 啟動後端，自動重新載入 |
| `uv run db-upgrade` | 更新資料庫結構 |
| `uv run test` | 執行後端測試與 coverage 檢查 |
| `uv run quality` | 執行 Python lint、格式檢查與完整測試 |
| `npm run build`（於 `frontend`） | 將前端建置至 `app/static`，前端修改後需重建 |
| `npm run quality`（於 `frontend`） | 執行前端型別、lint、格式、測試、建置與 smoke 檢查 |
| `npm run test:e2e`（於 `frontend`） | 執行 Playwright 瀏覽器測試 |

首次執行後端測試前，需先完成前端建置。

## 部署與版本發布

本專案目前由 **Drone + Portainer** 部署，流程定義於 [`.drone.yml`](.drone.yml)：

```text
main push → 品質檢查 → 版本檢查 → 更新 deploy 分支
          → Portainer 重新部署 → 健康檢查 → 建立版本 tag → Gitea Release
```

Portainer 使用 [`compose.portainer.yml`](compose.portainer.yml) 與 [`Containerfile`](Containerfile)，
由 `stack.env` 載入環境設定，將 `/data` 掛載至持久化儲存。容器埠為 `8000`，主機預設為 `3100`。

正式環境需設定 `APP_ENV=production`、`APP_SECRET_KEY`、Gitea OAuth 與 HTTPS cookie。
`APP_SECRET_KEY` 用來加密連線 token，變更後需重新輸入既有 token。
目前尚未提供完整角色權限，部署前請閱讀[安全指南](docs/security.md)。

版本以 [`pyproject.toml`](pyproject.toml) 的 `[project].version` 為準。合併至 `main` 前，使用
`uv version --bump patch --no-sync`（或 `--bump minor` / `--bump major`）同步更新版本與
`uv.lock`。若同名 tag 已指向其他 commit，部署會停止。

## 延伸文件

- [本機安裝與執行](docs/getting-started.md)
- [操作手冊](docs/user-guide.md)與 [API 使用指南](docs/api-guide.md)
- [系統架構](docs/architecture.md)與[品質檢查](docs/quality-gates.md)
- [環境設定範例](.env.example)與[疑難排解](docs/troubleshooting.md)
- [Portainer 部署設定](docs/deployment.md)、[CI/CD](docs/cicd.md)與[更新維運](docs/operations.md)
- [完整文件索引](docs/README.md)
