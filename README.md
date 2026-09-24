# Release Controller

Release Controller v3.0 是一套以 Drone 為執行端、以 Gitea Release 發布版本的
多專案 Release Worker / Orchestrator。每個專案可在 UI 設定 1～N 個元件、部署順序、
Drone/Gitea repository、target 與連線；操作者可為各元件獨立選擇 source build，再發布
單一元件或依設定順序執行 bundle。

後端使用 FastAPI、SQLAlchemy、Alembic 與 SQLite，保存穩定 workflow stage 與
append-only audit events；前端集中提供發布、workflow、排程、通知收件人、專案與連線管理。
既有 v1 approval workflow 與歷史資料仍完整保留。

## 為什麼建立這個專案

Jenkins 與 TeamCity 功能完整，但對目前的發布需求而言過於龐大，因此本專案選擇以
輕量的 Gitea + Drone 處理原始碼管理與 CI/CD。測試站可以在建置成功後自動部署，
但正式站不能採用完全無人介入的流程：部署前必須驗證操作者身分，並由具有權限的人員
進行審核。

正式站的發布因此不只是執行一條 pipeline，而是一個需要人類參與、具有核准節點與明確
狀態轉移的業務流程，這也是本專案導入 BPMN 與 workflow 的原因。

在建立 Release Controller 之前，正式站需要由人員手動執行 Drone promote，系統沒有
集中保存操作者、執行時間與對應 commit 的發布紀錄。本專案的目標是將 promote 納入
可追蹤的審核流程，記錄誰在何時核准並提交哪一個版本，接著自動觸發正式站部署，讓正式
發布同時具備人工管控、自動化與可稽核性。

目前版本已實作核准 workflow、promotion orchestration、部署與發布狀態、排程、通知及事件
歷程，並以 Gitea OAuth2 + PKCE 驗證操作人員。專案與連線的寫入可選擇限制為 Gitea
administrator；其餘操作尚未細分角色權限。

目前版本包含：

- 在 UI 建立多個專案、Drone/Gitea 連線及每個專案 1～N 個自訂元件；連線 token 加密保存，
  並支援預設、專案與元件層級覆寫及連線測試。
- 從 Drone 同步的 repository metadata 與實際 build history 動態取得 branch；每個元件各自
  選擇 build，不需要 branch 環境變數。
- 支援任意元件子集的單一 promotion 或循序 Bundle promotion，執行順序由專案設定決定。
- source build / promotion build 分離追蹤、timeout，以及由資料庫唯一條件保護的並行防重複 promotion。
- `FAILED` stage、stable error code、`CANCELLED` reason 與 workflow event history。
- Deployment 與 Publish 狀態分離，支援 Gitea Release、Tag conflict、冪等重送、
  Bundle 部分失敗、略過不需發布的元件，以及只重試失敗元件。
- 支援 IANA 時區的持久化部署排程與任意元件子集，並由單一背景 worker 重用既有
  orchestrator 觸發、追蹤及復原中斷的工作。
- workflow event SMTP 通知 outbox、去重、claim timeout 與 exponential retry。
- 建立單一元件、Bundle 或排程時可附加通知檔案；附件隨終態通知寄出，預設上限為 10 MiB。
- Gitea OAuth2 Authorization Code + PKCE、server-side session，以及可信 actor audit 欄位。
- 保留 v1 `PENDING -> APPROVED/REJECTED -> DEPLOYING -> SUCCESS/FAILED` API 與資料。
- 依專案元件動態產生 BPMN 定義及 UI stage timeline，涵蓋既有核准、單一元件、Bundle
  與排程流程，並清楚分隔 Deployment 與 Publish。
- FastAPI OpenAPI 文件、Alembic migration、測試與多階段 Containerfile。

> 目前尚未實作完整的角色型權限、service token 或 Gitea webhook 驗章；本 repository 的
> `.drone.yml` 負責自身品質檢查與 container 部署，下游元件 pipeline 仍由各自 repository
> 管理，Controller 只呼叫既有 Drone promotion API。服務只適合綁定 loopback、受信任內網，
> 或置於額外受保護的 reverse proxy 後方；正式開放前請先閱讀[安全指南](docs/security.md)。

## v3.0 正式環境設定範例

正式 token 只放在 server env file，不得 commit：

```env
APP_ENV=production
DATABASE_URL=sqlite:////data/release.db
DRONE_SERVER=http://drone.example.test
DRONE_TOKEN={drone_token}
DRONE_TIMEOUT_SECONDS=10
GITEA_SERVER=https://gitea.example.com
GITEA_TOKEN=CHANGE_ME
GITEA_TIMEOUT_SECONDS=10
APP_SECRET_KEY=CHANGE_ME
PROJECT_ADMIN_WRITES=false
AUTH_ENABLED=true
GITEA_OAUTH_CLIENT_ID=CHANGE_ME
GITEA_OAUTH_CLIENT_SECRET=CHANGE_ME
GITEA_OAUTH_CALLBACK_URL=https://release-controller.example.com/api/v1/auth/callback
AUTH_SESSION_HOURS=8
AUTH_COOKIE_SECURE=true
DEPLOYMENT_TIMEOUT_SECONDS=1800
BACKGROUND_WORKER_ENABLED=true
BACKGROUND_WORKER_POLL_SECONDS=5
SCHEDULER_BATCH_SIZE=20
RECOVERY_ENABLED=true
RECOVERY_BATCH_SIZE=25
DEPLOYMENT_POLL_INTERVAL_SECONDS=15
PUBLISH_TIMEOUT_SECONDS=600
SCHEDULE_CLAIM_TIMEOUT_SECONDS=900
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_FROM_ADDRESS=release-controller@example.com
SMTP_DEFAULT_RECIPIENTS=release-team@example.com
SMTP_TLS_VERIFY=true
NOTIFICATION_ATTACHMENT_MAX_BYTES=10485760
```

完整欄位、預設值與 SMTP/TLS 選項請以 [`.env.example`](.env.example) 為準。上面的
`DRONE_SERVER` / `DRONE_TOKEN` 與 `GITEA_SERVER` / `GITEA_TOKEN` 供全域健康檢查及 v2.x
升級種子使用；實際發布路徑會解析 Connections 中保存的連線。

`APP_SECRET_KEY` 用來加密存在 `upstream_connections` 的 Drone / Gitea token，
`APP_ENV=production` 時未設定會拒絕啟動。改掉它會讓既有的 token 解不開，UI 會把那些連線
標成 `key_mismatch`，需要重新輸入。

**v3.0 起，「要發哪些 repository、順序如何、target 是什麼」是專案設定，不是環境變數**，
請在畫面上的 Projects 與 Connections 兩頁維護。

從 v2.x 升級時，migration `20260902_0014` 會讀一次
`DRONE_FRONTEND_REPO_OWNER` / `_NAME`、`DRONE_BACKEND_REPO_OWNER` / `_NAME`、
對應的 `GITEA_*` 與 `DRONE_DEFAULT_TARGET`，把現有設定變成第一個專案。**只有那一次升級需要它們**；
全新安裝不必設定，登錄表會是空的，直接在 UI 建專案即可。

不需設定 `DRONE_*_BRANCH`。Controller 會向 Drone 查詢實際 Gitea repository metadata，
並從近期 build history 產生 branch 清單；專案的 default target 是部署 target，並非 branch。

## 快速開始（Windows PowerShell）

需求：uv、Python 3.10+、Node.js 20.19+（或 22.12+）與 npm。

```powershell
Copy-Item .env.example .env
# 若本機尚未設定 Gitea OAuth，請在 .env 設定 AUTH_ENABLED=false。
# 使用本機 HTTP 時，AUTH_COOKIE_SECURE 必須是 false。
uv sync

Set-Location frontend
npm ci
npm run build
Set-Location ..

uv run db-upgrade
uv run dev
```

`uv sync` 會依 `uv.lock` 建立或更新 `.venv`，不需手動啟用虛擬環境。啟動後開啟：

- 操作介面：<http://127.0.0.1:8000/>
- API 文件：<http://127.0.0.1:8000/docs>
- 健康檢查：<http://127.0.0.1:8000/health>
- Gitea 連線檢查：<http://127.0.0.1:8000/api/v1/gitea/health>

完整的 Windows、Linux/macOS 安裝與日常啟動方式請見
[本機安裝與執行](docs/getting-started.md)。

`AUTH_ENABLED=false` 僅適合本機開發。正式環境應啟用 Gitea OAuth，並在 HTTPS 下設定
`AUTH_COOKIE_SECURE=true`。若要使用排程通知與附件，還必須完成最新 migration、SMTP 設定，
並只啟動一個 background worker；附件內容會保存在 SQLite，因此資料庫備份也會包含附件。

## 文件

文件首頁與閱讀順序請見 [docs/README.md](docs/README.md)。

| 文件 | 用途 |
| --- | --- |
| [本機安裝與執行](docs/getting-started.md) | 開發環境、migration、前後端啟動與測試 |
| [前端操作手冊](docs/user-guide.md) | 建立 release、核准、部署與 BPMN 畫面判讀 |
| [React + TypeScript 求職導讀](docs/react-typescript-guide.md) | 元件、Hook、泛型函式的效用、使用場景與面試說法 |
| [測試圍欄與品質閘門](docs/quality-gates.md) | Ruff、coverage、ESLint、Vitest、Playwright 與 Drone |
| [自動 CI/CD](docs/cicd.md) | main push、Drone runners 與 Podman 自動部署 |
| [API 使用指南](docs/api-guide.md) | API payload、回應、篩選與完整 curl 流程 |
| [系統架構](docs/architecture.md) | 元件、資料模型、SpiffWorkflow 與目錄結構 |
| [Podman 部署](docs/podman-deployment.md) | 使用 Gitea VM 首次建置及 Container 部署 |
| [更新與維運](docs/operations.md) | 版本更新、監控、備份、還原與回滾 |
| [CI/CD 整合](docs/ci-integration.md) | Drone/Gitea 的建議整合流程與責任分工 |
| [安全指南](docs/security.md) | 現況限制、token/OAuth 選擇與上線檢核 |
| [疑難排解](docs/troubleshooting.md) | 常見啟動、資料庫、前端與 Podman 問題 |
| [v3.0 多專案規格](docs/specifications/MULTI_PROJECT_V3_0_SPEC.md) | 多專案、1～N 元件、連線登錄表與遷移設計 |
| [原始規格](docs/specifications/RELEASE_CONTROLLER_SPEC.md) | 專案需求、範圍與驗收條件 |
| [v2.2 規格](docs/specifications/RELEASE_WORKER_V2_2_SPEC.md) | Release Worker、Drone 與 backend-first workflow |

## 最常用指令

```powershell
# 後端測試
uv run test

# 完整 Python gate（Ruff、格式與 coverage）
uv run quality

# 前端程式修改後重新產出 app/static
Set-Location frontend
npm run quality
Set-Location ..

# migration 狀態
uv run db-current
uv run db-upgrade
```

前端的 `npm run quality` 包含 typecheck、ESLint、Prettier、Vitest、build 與 smoke test；
需要瀏覽器 E2E 時，另在 `frontend` 目錄執行 `npm run test:e2e`。

Container 的固定契約為：容器埠 `8000`、健康檢查 `/health`、資料庫
`/data/release.db`；部署腳本從 runtime env file 讀取 `HOST_IP`，預設埠為 `3100`。
部署前請填入原本的綁定位址；也可用 shell 的 `HOST_IP` 與 `HOST_PORT` 覆寫。
Gitea VM 的完整操作請勿只照此摘要，請使用
[Podman 部署](docs/podman-deployment.md)與[更新與維運](docs/operations.md)。

## 調整發布版本

自動發布以 `pyproject.toml` 的 `[project].version` 為唯一版本來源。請在合併或 push 到
`main` 前，從 repository root 更新版本；輸入版本時不要加 `v`，Drone 會在建立 tag 時
自動加上。

```powershell
# 指定下一版，例如 2.4.0 → 2.5.0；同步更新 pyproject.toml 與 uv.lock
uv version 2.5.0 --no-sync

# 或依語意化版本自動遞增
uv version --bump patch --no-sync  # 2.4.0 → 2.4.1
uv version --bump minor --no-sync  # 2.4.0 → 2.5.0
uv version --bump major --no-sync  # 2.4.0 → 3.0.0

# 確認準備發布的版本與變更
uv version --short
git diff -- pyproject.toml uv.lock
```

版本號必須使用語意化版本格式，且已發布的版本不能指向另一個 commit。確認版本後再
commit 並 push/merge 到 `main`；quality 與部署成功後，Drone 會建立 `v<version>` tag，
再由 tag pipeline 建立同版本的 Gitea Release。

## 部署 v3.0

確認 runtime env file 已加入上述 Gitea 設定且權限為 `600`，再從 repository root 執行：

```bash
chmod 600 ~/services/release-controller/config/release-controller.env
bash ./scripts/deploy-release-controller.sh "$PWD"
curl -fsS "http://<HOST_IP>:3100/health"
curl -fsS http://127.0.0.1:3100/api/v1/gitea/status
```

`main` push 的 Drone deploy pipeline 會先檢查 `pyproject.toml` 版本的 `v<version>` tag。
若同名 tag 已指向不同 commit，流程會在替換 container 前停止；請先遞增版本並同步更新
`uv.lock`，再 commit 並 push。既有 tag 不會移動。檢查通過且 container health check
成功後，流程才建立並 push 同 commit 的 tag；tag pipeline 接著使用官方
`plugins/gitea-release` 建立 Gitea Release。Drone repository 必須設定具有 release 建立權限的
`gitea_release_token` 與 Gitea 服務 URL 的 `gitea_server` secrets。
