# 疑難排解

## 前端顯示 Service unavailable

先直接查 health：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health
```

- 連線拒絕：Uvicorn 沒啟動或 port 錯誤。
- HTTP 503：服務在，但資料庫連線/權限有問題。
- HTTP 200：檢查瀏覽器 Console/Network 是否是錯誤 host、cache 或 API 失敗。

VM 上改用：

```bash
curl -v http://127.0.0.1:3100/health
podman logs --tail 200 release-controller
```

## API 顯示 `Unexpected token '<'` 或回傳 `<!doctype html>`

前端收到的是 HTML 首頁而不是 JSON API，通常是新前端搭配舊後端，或 reverse proxy 把
未知 `/api/*` 路徑導回首頁。先直接檢查 endpoint：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/notifications/recipients
```

正確回應的 `Content-Type` 是 `application/json`。若看到 HTML，本機請套 migration 並完整
重啟 Uvicorn：

```powershell
uv run db-upgrade
uv run dev
```

VM 請重新 build／替換 container，再確認 API；只複製 `app/static` 不算完整部署：

```bash
curl -i http://127.0.0.1:3100/api/v1/notifications/recipients
podman logs --tail 200 release-controller
```

## 本機 port 8000 已被占用

PowerShell：

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
```

確認 process 後結束舊服務，或使用其他 port：

```powershell
uv run dev --port 8001
```

網址也要改成 `http://127.0.0.1:8001/`。

## `Activate.ps1` 被 execution policy 阻擋

不需修改全機 policy 或手動啟用 `.venv`，由 uv 執行即可：

```powershell
uv run test
uv run db-upgrade
uv run dev
```

## `no such table: releases`

尚未執行 migration，或 `DATABASE_URL` 指到另一個 database：

```powershell
Get-Content .env
uv run db-current
uv run db-upgrade
```

修改 `.env` 後重啟 process。不要在不確定的 database 上執行 downgrade。

## 前端是舊版或空白

FastAPI 服務的是 `app/static`。修改 `frontend/src` 後重新 build：

```powershell
Set-Location frontend
npm ci
npm run build
Set-Location ..
```

重新啟動 Uvicorn，瀏覽器 hard refresh。若 build 失敗，確認 Node 20.19+（或 22.12+）
並保留完整 npm
錯誤輸出。Container build 會在 Node stage 自動執行 `npm ci` 與 `npm run build`。

## 建立 release 回 409

同一組 `repository + commit_sha + environment` 已存在，或重複執行狀態轉移。先查詢：

```bash
curl -fsS "http://127.0.0.1:8000/api/v1/releases?repository=REPO&environment=ENV"
```

不要藉由更改 commit SHA 字串格式規避唯一限制；CI 應保存既有 `release_id`。

## 按鈕不見或不能按

前端只顯示目前狀態允許的動作：

- `PENDING` 才有 Approve/Reject。
- `APPROVED` 才有 Start deployment。
- `DEPLOYING` 才有 Mark success/failed。
- 完成狀態沒有後續按鈕。

選取 release 並查看畫面上方 status；也可呼叫 `GET /api/v1/releases/{id}`。

## BPMN 沒有同步

頁面可見時每 5 秒同步。按右上 refresh；再檢查：

```bash
curl -fsS http://127.0.0.1:8000/api/v1/workflows/release-definition
curl -fsS http://127.0.0.1:8000/api/v1/releases/RELEASE_ID/workflow
```

若 release status 已變但 workflow endpoint 500，保留 database 備份及 server log 再分析，
不要直接編輯 `release_workflows.state_json`。

## Podman build 無法下載依賴

Container build 需存取 base image registry、npm 與 PyPI。檢查 VM 的 DNS、proxy、CA 與
registry mirror：

```bash
podman pull node:20-bookworm-slim
podman pull python:3.10-slim-bookworm
podman build --log-level=debug -f Containerfile -t localhost/release-controller:test .
```

公司使用 TLS inspection 時，應由維運人員把 CA 正確安裝到 host/build environment；
不要以關閉 TLS 驗證當長期解法。

## Container 一啟動就退出

```bash
podman ps -a --filter name=release-controller
podman logs release-controller
podman inspect release-controller --format '{{.State.ExitCode}} {{.State.Error}}'
```

常見原因是 migration、`/data` 權限、錯誤的 `DATABASE_URL` 或 port 衝突。標準值是
`sqlite:////data/release.db`，四個斜線代表絕對路徑。

## `/data` permission denied

確認 bind mount 與 host 目錄：

```bash
podman inspect release-controller --format '{{json .Mounts}}'
ls -ld "$HOME/services/release-controller/data"
ls -l "$HOME/services/release-controller/data"
```

所有 Podman 指令使用同一個 rootless 帳號，SELinux host 掛載時保留 `:Z`。不要直接
`chmod -R 777`；應由 VM 維運人員依 user namespace 設定正確 owner/label。

## VM 可以 curl，本機瀏覽器不能開

因為服務只綁 VM loopback，需在本機保持 SSH tunnel：

```bash
ssh -v -N -L 3100:127.0.0.1:3100 <VM_USER>@<VM_HOST>
```

本機再開 `http://127.0.0.1:3100/`。若本機 port 被占用，改成
`-L 13100:127.0.0.1:3100` 並開 `http://127.0.0.1:13100/`。

## 需要提供哪些診斷資訊

回報問題時，提供以下資訊並先移除 secret：

- Git commit：`git log -1 --oneline`。
- Python/Node/Podman 版本。
- `/health` HTTP status 與 body。
- `podman ps -a` 與相關時間範圍的 log。
- Alembic current revision。
- 操作前 release status、呼叫 endpoint 與 HTTP status。

不要傳送 `.env`、Authorization header、SSH key 或完整 production database。
