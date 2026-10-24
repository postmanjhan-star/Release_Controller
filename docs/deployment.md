# Portainer 部署設定

目前使用 Drone 通過品質檢查後，更新 `deploy` 分支並要求 Portainer 重新部署。
首次建立 Stack 時使用本頁；自動部署所需 secrets 見 [CI/CD](cicd.md)。

## 建立 Stack

1. 確認 Portainer 管理的 Docker 環境可以存取 Gitea、容器 registry、npm 與 PyPI。
2. 建立指向本 repository 的 Git Stack，追蹤 `refs/heads/deploy`，Compose path 設為
   `compose.portainer.yml`。首次建置前，該分支必須已存在且包含這份設定。
3. 私有 repository 的讀取憑證放在 Portainer；這與 Drone 推送分支的憑證分開設定。
4. 設定下表的 Compose 變數與應用程式環境，確認 `stack.env` 可由 Stack 載入。
5. 部署後確認容器 log、`/health` 與登入畫面，再接上 Drone 自動部署。

請明確選擇 [`compose.portainer.yml`](../compose.portainer.yml)，不要使用根目錄舊的
`docker-compose.yml`；後者仍引用不存在的 `Dockerfile` 與特定主機路徑。

## 環境設定

Compose 插值變數決定主機的埠與掛載，需在 Portainer 的 Stack 環境變數中設定：

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| `HOST_IP` | `127.0.0.1` | 主機綁定位址；依 reverse proxy 與 Drone runner 的可達路徑設定 |
| `HOST_PORT` | `3100` | 主機對外埠 |
| `DATA_DIR` | `./data` | `/data` 的主機來源；正式環境請填固定絕對路徑 |

`stack.env` 提供容器內的應用程式設定。可參考 [`.env.example`](../.env.example)，至少確認：

| 設定 | 正式環境要求 |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_SECRET_KEY` | 高熵隨機值，用於加密連線 token；保存並備份 |
| `AUTH_ENABLED` | `true` |
| `GITEA_SERVER` | 操作人員登入所用的 Gitea URL |
| `GITEA_OAUTH_CLIENT_ID` / `GITEA_OAUTH_CLIENT_SECRET` | Gitea OAuth application 憑證 |
| `GITEA_OAUTH_CALLBACK_URL` | `https://<服務網域>/api/v1/auth/callback`，與 Gitea 登錄值一致 |
| `AUTH_COOKIE_SECURE` | HTTPS 正式入口設為 `true` |
| `BACKGROUND_WORKER_ENABLED` | 使用排程、背景追蹤及通知時設為 `true` |

SMTP 設定見[更新與維運](operations.md)。不要將 `stack.env` 或憑證提交至 Git。
Compose 會固定覆寫 `DATABASE_URL=sqlite:////data/release.db`，不會沿用開發用的相對路徑。

全新安裝後，在 UI 的 **Projects → Connections** 建立 Drone / Gitea 連線，再設定專案元件。
Repository、順序與 target 由專案設定管理；舊版 repo 環境變數只供首次升級 migration 匯入。

## 容器契約與驗證

[`Containerfile`](../Containerfile) 先建置前端，啟動時執行 Alembic migration，再啟動單一
Uvicorn worker。容器埠為 `8000`，`/data` 必須持久化，SQLite 部署維持單一服務 instance。

在實際 Docker 主機執行（URL 換成設定的綁定位址與埠）：

```bash
docker ps --filter name=release-controller
docker logs --tail 100 release-controller
curl -fsS http://127.0.0.1:3100/health
```

也可從 Portainer 查看容器狀態及 log。健康檢查只確認應用程式與資料庫可用；登入後另測試
Connections 與專案驗證，確認 Drone / Gitea 的權限及 repository mapping。

Drone 的 `verify-deployment` 使用 repository secret `deployment_health_url`，必須填完整的
Release Controller `/health` URL；沒有預設主機，也不會自動讀取 `HOST_IP` / `HOST_PORT`。
更換主機或埠時更新此 secret，無需修改 `.drone.yml`。例如家用 NAS 為 `10.0.4.123`，
且服務發布在該位址的 `3100` 埠時，可填 `http://10.0.4.123:3100/health`，但必須先從
Drone 建置容器測試連通；NAS 主機或瀏覽器能連通，不能代表建置容器也可連通。
`HOST_IP` 預設只綁 `127.0.0.1`，此時 NAS 區網 IP 的入口不會開放。

如果建置容器無法連到 NAS 發布埠，可讓它與應用程式加入同一個 Docker 自訂 bridge network，
使用 `http://release-controller:8000/health`。只讓 Portainer 加入共同網路還不夠，
`release-controller` 也必須加入。以下以已建立的 `ci-portainer` 為例，將設定合併到 Stack
實際使用的 Compose，並在 Git 中保留，讓重新部署後仍有效：

```yaml
services:
  release-controller:
    # 保留原有 build、ports、volumes、environment 等設定
    networks:
      - default
      - ci-portainer

networks:
  default: {}
  ci-portainer:
    external: true
    name: ci-portainer
```

Docker runner 的 `DRONE_RUNNER_NETWORKS` 也需包含 `ci-portainer`，讓每個建置容器加入網路。
可在 NAS 先驗證：

```sh
docker run --rm --network ci-portainer curlimages/curl:latest \
  --noproxy '*' --connect-timeout 5 --max-time 10 \
  -fsS http://release-controller:8000/health
```

這個 URL 指向應用程式的容器埠 `8000`；Portainer 的 `9000` 是管理 API 入口。
Compose 內部的 `127.0.0.1:8000/health` 則由應用程式容器自己執行，維持原設定。
部署流程沒有自動回滾，更新前請依[維運文件](operations.md)備份。
