# Release Controller 的 Drone CI/CD

本專案使用根目錄的 `.drone.yml`，不再使用 Gitea Actions：

```text
push / pull request -> quality (Docker runner)
main push          -> quality -> deploy (Docker runner -> SSH) -> Podman -> /health -> Gitea Release
```

`quality` 在隔離的 containers 中執行 Python lint、tests、coverage、Frontend quality 及
Playwright E2E。`deploy` 只接受 `main` push，且必須等 `quality` 成功；既有 Docker runner
會執行 `appleboy/drone-ssh:1.8.0`，再以 SSH 連到 production VM。

部署時不使用 Drone 的暫存 checkout。SSH step 會讓 production VM 上既有的
`$HOME/services/release-controller-src` checkout 本次 `DRONE_COMMIT_SHA`，再執行
該 commit 內的 `scripts/deploy-release-controller.sh`，讓部署檢查與應用程式版本同步。
健康檢查成功後，同一 SSH step 會從 `pyproject.toml` 取得版本、確認 checkout 仍是本次
`DRONE_COMMIT_SHA`，再建立並 push 同 commit 的 `v<version>` tag。該 tag 事件會啟動獨立的
`release` pipeline，由官方 `plugins/gitea-release` 建立 Gitea Release。既有 tag 若指向
不同 commit，pipeline 會停止且不移動 tag。
Repository 部署腳本從 runtime env file 的 `HOST_IP` 讀取綁定位址，預設埠為 `3100`；
部署前請將原本使用的位址填入該檔案。Shell 的 `HOST_IP` 優先，也支援 `HOST_PORT`、`CONTAINER_NAME`、
`IMAGE_NAME`、`NETWORK_NAME`、`DATA_DIR` 與 `ENV_FILE` overrides。

## 1. 在 Drone 啟用 repository

使用對 `example-owner/Release_Controller` 有管理權限的帳號登入 Drone：

1. 執行 repository sync。
2. 找到 `example-owner/Release_Controller`。
3. 選擇 **Activate/Enable Repository**。
4. 到 Gitea repository 的 **Settings -> Webhooks**，確認 Drone webhook 已建立。

只有 push 不足以啟用尚未 Activate 的 Drone repository。啟用後，Drone 才會接收 Gitea
webhook、讀取 `.drone.yml` 並建立 build。

## 2. Quality Docker runner

`quality` 使用既有 Drone Docker runner，會拉取：

- `python:3.10-slim-bookworm`
- `node:20.20.2-bookworm-slim`
- `mcr.microsoft.com/playwright:v1.62.1-noble`

Docker runner 不需要 production env file 或 Podman 主機權限。若內網不能存取上述
registries，應先同步 images 到內部 registry，再修改 `.drone.yml` 的 image URL。

## 3. Production SSH deployment

`deploy` 由既有 Docker runner 啟動 Drone SSH plugin，plugin
再登入 production VM。Release Controller repository 必須設定以下 Drone secrets：

| Secret | 用途 |
| --- | --- |
| `production_host` | production VM hostname 或 IP |
| `production_user` | 操作 rootless Podman 的 Linux 帳號 |
| `production_ssh_key` | 上述帳號接受的 SSH private key |
| `gitea_server` | Gitea 的完整服務 URL，值與 runtime env 的 `GITEA_SERVER` 相同 |
| `gitea_release_token` | 對 Release Controller repository 具有建立 release 權限的 Gitea token |

private key 只存於 Drone secret，不得加入 `.drone.yml`、repository 或 build log。對應的
public key 必須加入 production VM 帳號的 `~/.ssh/authorized_keys`。

遠端帳號必須能更新 source checkout、讀取 repository 內的部署腳本並操作 rootless Podman：

```bash
test -d "$HOME/services/release-controller-src/.git"
cd "$HOME/services/release-controller-src"
git status --short
git fetch --prune origin
git checkout --detach COMMIT_SHA
test -r scripts/deploy-release-controller.sh
podman info
```

source checkout 不得有未提交變更。遠端 repository credential 必須允許 non-interactive
`git fetch`，並允許在部署成功後 push `v*` tag（不需要 branch 寫入權限）；pipeline 會
checkout 觸發該 build 的精確 commit，而不是部署執行當下最新的 `main`。若 repository
啟用 tag protection，部署帳號也必須被允許建立對應的 `v*` tag。

## 4. Runtime env

production secrets 仍只放在部署 VM，不放進 `.drone.yml` 或 Drone build log：

```bash
mkdir -p "$HOME/services/release-controller/config"
chmod 700 "$HOME/services/release-controller/config"
chmod 600 "$HOME/services/release-controller/config/release-controller.env"
test -r "$HOME/services/release-controller/config/release-controller.env"
podman info
```

部署腳本預設讀取：

```text
$HOME/services/release-controller/config/release-controller.env
```

其中包含 Release Controller runtime 使用的 `DRONE_TOKEN`、`GITEA_TOKEN`、`APP_SECRET_KEY`、
OAuth 與 SMTP 設定，以及部署綁定位址 `HOST_IP`（純值，不加引號）。缺少 `HOST_IP` 時
腳本會在建置或替換容器前停止。v3.0 的 repository mapping 改由專案設定管理；舊版的
`DRONE_*_REPO_OWNER/NAME`、`GITEA_*_REPO_OWNER/NAME` 僅供首次升級時匯入既有設定，
不再是部署必填項目。SSH private key 與自動發布使用的 `gitea_release_token` 則只保存
在 Drone repository secrets，不放進 runtime container。`gitea_server` 也需在 Drone 設定；
CI 不會自動讀取本機或部署 VM 的 `.env`。

## 5. 首次驗證

1. Activate repository，確認 Docker runner online，並建立三個 `production_*` secrets 與
   `gitea_release_token`、`gitea_server`。
2. push feature branch 或建立 pull request，確認只執行 `quality`。
3. push/merge 到 `main`，確認 `quality` 成功後執行 deploy；部署成功 push tag 後，再確認
   tag event 啟動 `release` pipeline。
4. 在 VM 驗證：

```bash
curl -fsS "http://<HOST_IP>:3100/health"
podman ps --filter name=release-controller
podman logs --tail 100 release-controller
```

並確認 Gitea repository 的 Releases 頁面已建立與 `pyproject.toml` 相同版本的 tag/release。

5. 第一次 build 回報 Gitea commit status 後，把 Drone quality status 設成 `main` branch
   protection 的 required status check。

`deploy` 設有 concurrency limit 1，不會同時替換同一個 container。部署失敗時，腳本會
輸出最後 100 行 container log，並嘗試啟動 rollback image。

## 常見失敗

- Drone 找不到 repository：先 sync，並確認登入帳號對 Gitea repository 有管理權限。
- push 沒有 build：repository 尚未 Activate，或 Gitea webhook delivery 失敗。
- `quality` pending：Drone Docker runner 不在線或無法拉取 images。
- `deploy` pending：Docker runner 離線、沒有 capacity，或無法拉取 Drone SSH plugin image。
- `secret not found`：Release Controller repository 缺少對應的 `production_*` secret。
- SSH handshake/authentication 失敗：檢查 host、user、private key 及遠端 `authorized_keys`。
- `git fetch`／`git checkout` 失敗：source checkout 有本機修改，或遠端帳號沒有 Gitea
  repository 的 non-interactive 存取權限。
- `git push refs/tags/...` 失敗：遠端 repository credential 沒有 tag 寫入權限，或 Gitea
  tag protection 不允許部署帳號建立 `v*` tag。
- `Podman is required`：`production_user` 不是目前操作 rootless Podman 的 Linux 帳號，或其
  `PATH` 找不到 Podman。
- `Runtime env file was not found`：SSH 登入帳號的 `$HOME` 不含 production runtime env。
- `runtime env 缺少或為空：DRONE_FRONTEND_REPO_OWNER`：檢查 build 的 `.drone.yml` 是否仍
  呼叫 VM 上的 `$HOME/deploy-release-controller-v2.2.sh`。v3.0 應改用該 commit 內的
  `scripts/deploy-release-controller.sh`；推送修正後觸發新 build，重跑舊 commit 仍會使用舊設定。
- health check 失敗：從 Drone build log 及 `podman logs release-controller` 檢查。
- publish 失敗：確認 Drone `gitea_release_token` 對 Release Controller repository 有建立
  release 權限；若訊息指出 tag 指向不同 commit，必須人工確認版本號是否忘記遞增，
  pipeline 不會覆寫既有 tag。
