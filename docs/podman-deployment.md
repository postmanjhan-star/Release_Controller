# 使用 Gitea VM 的 Podman 部署

本機不需要安裝 Docker 或 Podman。開發者在本機測試並 push Git；Gitea VM clone/pull
程式碼後，使用 VM 上的 Podman build image 並啟動 container。

```text
Developer PC --git push--> Gitea
                              |
Gitea VM --------git pull-----+
   | podman build
   +--> localhost/release-controller:<version>
   +--> release-controller container
   +--> ~/services/release-controller/data/release.db
```

## 前置確認

VM 需要：

- Git 與 Podman。
- 可讀取 Gitea repository 的 SSH key 或帳密。
- build 時可存取 base image registry、npm registry、PyPI；或已配置內部 mirror。
- 未被占用的 `127.0.0.1:3100`。
- 足夠空間存放 source、image 與 SQLite 備份。

登入 VM 後檢查：

```bash
podman --version
git --version
podman info
ss -ltn | grep ':3100' || true
```

若是 rootless Podman，所有 Podman 指令都應使用同一個 Linux 使用者執行；不要混用
`sudo podman` 與 `podman`，否則會看到不同的 image/container/volume。

## 第一次取得程式

以下路徑與帳號請換成 VM 實際值：

```bash
mkdir -p "$HOME/services"
cd "$HOME/services"
git clone https://gitea.example.com/example-owner/Release_Controller.git release-controller-src
cd release-controller-src
git status
git log -1 --oneline
```

若 Gitea 支援 SSH，正式環境建議使用 deploy key 與 SSH clone URL，避免把密碼或 token
寫在 shell history。不要將 production secret 放入 repository。

## 方式 A：使用 repository 部署腳本

Repository 已提供 `scripts/deploy-release-controller.sh`。第一次執行前先閱讀腳本，
確認 image、container、port、volume 與本文契約相同：

```bash
less scripts/deploy-release-controller.sh
```

從 repository 根目錄部署：

先建立只由服務帳號讀取的 v2.3 Drone 與 Gitea 設定：

```bash
mkdir -p "$HOME/services/release-controller/config"
cat > "$HOME/services/release-controller/config/release-controller.env" <<'EOF'
HOST_IP=127.0.0.1
DRONE_SERVER=http://{IP_ADDRESS}:{PORT}
DRONE_TOKEN=REPLACE_ME
DRONE_FRONTEND_REPO_OWNER=
DRONE_FRONTEND_REPO_NAME=SMT-Assistant
DRONE_BACKEND_REPO_OWNER=
DRONE_BACKEND_REPO_NAME=soda
DRONE_DEFAULT_TARGET=pre-production
DRONE_TIMEOUT_SECONDS=10

GITEA_SERVER=https://gitea.example.com
GITEA_TOKEN=REPLACE_ME
AUTH_ENABLED=true
GITEA_OAUTH_CLIENT_ID=REPLACE_ME
GITEA_OAUTH_CLIENT_SECRET=REPLACE_ME
GITEA_OAUTH_CALLBACK_URL=https://release-controller.example.com/api/v1/auth/callback
AUTH_SESSION_HOURS=8
AUTH_COOKIE_SECURE=true
GITEA_FRONTEND_REPO_OWNER=
GITEA_FRONTEND_REPO_NAME=SMT-Assistant
GITEA_BACKEND_REPO_OWNER=
GITEA_BACKEND_REPO_NAME=soda
GITEA_TIMEOUT_SECONDS=10
DEPLOYMENT_TIMEOUT_SECONDS=1800

# IBM Domino / Notes SMTP
BACKGROUND_WORKER_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_SSL=false
SMTP_TLS_VERIFY=true
SMTP_FROM_ADDRESS=xxx@gmail.com
SMTP_DEFAULT_RECIPIENTS=xxxx@gmail.com
EOF
chmod 600 "$HOME/services/release-controller/config/release-controller.env"
```

先在 Gitea 的 `Settings → Applications` 建立 OAuth2 application，redirect URI 必須與
`GITEA_OAUTH_CALLBACK_URL` 完全相同。正式環境需由 HTTPS reverse proxy 對外提供服務；
只有本機 HTTP 測試可使用 `AUTH_COOKIE_SECURE=false`。

`gmail.com` 的 SMTP 端點目前是 `smtp.gmail.com:587`，並支援 STARTTLS。
正式啟用 worker 前，請先在 Gitea VM 驗證 TLS 憑證與 SMTP 路徑：

```bash
openssl s_client \
  -connect smtp.gmail.com:587 \
  -starttls smtp \
  -servername smtp.gmail.com \
  -verify_return_error </dev/null
```

若出現憑證過期或不受信任，應請 Domino 管理員更新憑證，或把公司 CA 的 PEM 檔以
read-only volume 掛入 Release Controller，再設定 `SMTP_TLS_CA_FILE`。不要為了上線而
長期使用 `SMTP_TLS_VERIFY=false`。

Repository 的部署腳本會將上述檔案傳給 `podman run`：

```bash
--env-file "$HOME/services/release-controller/config/release-controller.env"
```

然後從 repository 根目錄部署：

```bash
cd "$HOME/services/release-controller-src"
bash ./scripts/deploy-release-controller.sh "$PWD"
curl -fsS http://127.0.0.1:3100/health
curl -fsS http://127.0.0.1:3100/api/v1/gitea/status
podman ps --filter name=release-controller
```

腳本負責建立 `release-net`、build image、保留 rollback image、替換 container、掛載
資料目錄、執行 health check，失敗時嘗試回滾。

## 方式 B：沒有腳本時手動部署

以下指令符合目前 Containerfile 與規格契約：

```bash
cd "$HOME/services/release-controller-src"

podman network exists release-net || podman network create release-net
mkdir -p "$HOME/services/release-controller/data"
test -r "$HOME/services/release-controller/config/release-controller.env"

podman build \
  --file Containerfile \
  --tag localhost/release-controller:latest \
  .

podman rm --force release-controller 2>/dev/null || true

podman run --detach \
  --name release-controller \
  --network release-net \
  --restart unless-stopped \
  --publish 127.0.0.1:3100:8000 \
  --volume "$HOME/services/release-controller/data:/data:Z" \
  --env-file "$HOME/services/release-controller/config/release-controller.env" \
  --env APP_ENV=production \
  --env LOG_LEVEL=INFO \
  --env DATABASE_URL=sqlite:////data/release.db \
  localhost/release-controller:latest
```

啟動時 container 會先執行 `alembic upgrade head`，然後才啟動 Uvicorn。確認：

```bash
podman logs --tail 100 release-controller
curl -fsS http://127.0.0.1:3100/health
curl -fsS http://127.0.0.1:3100/api/v1/gitea/status
podman inspect release-controller --format '{{.State.Status}}'
```

在沒有 SELinux 的系統，`:Z` 通常可保留；若平台不支援該 option，再依 VM 設定移除。
資料目錄必須可由 rootless container 寫入。權限問題見[疑難排解](troubleshooting.md)。

## 從本機開啟 VM 上的頁面

服務刻意只 publish 到 VM 的 `127.0.0.1:3100`，其他電腦不能直接連線。從本機建立
SSH tunnel：

```bash
ssh -N -L 3100:127.0.0.1:3100 "<VM_USER>@<VM_HOST>"
```

保持該終端開啟，再在本機瀏覽器開啟 <http://127.0.0.1:3100/>。若本機 3100 已占用，
可改左側埠，例如 `-L 13100:127.0.0.1:3100`，網址改為
`http://127.0.0.1:13100/`。

要讓受信任的 CI container 存取，可將它加入 `release-net`，並以
`http://release-controller:8000` 呼叫。若 CI 不在同一 VM/network，應使用受保護的
reverse proxy、TLS 與 authentication，不應直接把無驗證 API publish 到所有介面。

## Image 與資料的界線

- Image 含 Python dependencies、後端程式、BPMN XML 與已 build 的前端。
- `/data` bind mount 含 SQLite，替換 image/container 不會刪除 release 紀錄。
- 不要把 `release.db` COPY 進 image。
- 不要在更新時刪除 `~/services/release-controller/data`。

版本更新、備份與 rollback 請接著閱讀[更新與維運](operations.md)。
