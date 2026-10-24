# 疑難排解

先確認問題發生在本機、Drone pipeline、Portainer 建置或應用程式執行階段。
本機網址預設 `http://127.0.0.1:8000`；容器主機預設埠為 `3100`，以實際設定為準。

## 啟動與畫面

| 症狀 | 檢查方式 |
| --- | --- |
| 連線拒絕 | 確認 `uv run dev` 或容器已啟動、綁定位址與埠正確 |
| `/health` 回 503 | 檢查資料庫路徑、掛載與寫入權限 |
| 首頁空白或舊版 | 在 `frontend` 執行 `npm ci`、`npm run build`，完整重啟後端並重新整理 |
| API 回 HTML | 檢查前後端版本與 reverse proxy；`/api/*` 不應被導回首頁 |
| Port 8000 被占用 | 停止已確認的舊服務，或使用 `uv run dev --port 8001` |
| PowerShell 無法啟用 `.venv` | 直接用 `uv run`，不需要修改 execution policy |
| `no such table` | 確認 `DATABASE_URL`，執行 `uv run db-current`、`uv run db-upgrade` |

FastAPI 提供的是 `app/static`，不是 `frontend/src`。前端建置需求見[本機安裝](getting-started.md)。

## 登入與連線

- **OAuth 未設定／redirect 失敗**：確認 client ID、secret、Gitea URL 與 callback 完全一致。
- **登入後仍 401**：確認 session 是否過期、cookie 是否送出。HTTP 本機測試需
  `AUTH_COOKIE_SECURE=false`；正式環境使用 HTTPS。
- **設定寫入回 403**：檢查 `PROJECT_ADMIN_WRITES` 與 `/api/v1/auth/session` 的 `user.is_admin`。
- **`key_mismatch`**：目前 `APP_SECRET_KEY` 與加密時不同；恢復正確 key 或重新輸入連線 token。
- **沒有專案／build**：先建立連線、專案與啟用的元件，測試連線並驗證 repository / token 權限。
- **缺少 branch**：清單取自近期 build history 與 repository default branch，不是所有 Git branch 的完整列表。

匿名 curl 呼叫業務 API 回 401 是預期行為；`/health` 則不需登入。
上游全域健康檢查使用 env 設定，實際發布使用 Connections；請分別檢查。

## 部署、發布與排程

| 症狀 | 檢查方式 |
| --- | --- |
| Promotion 回 409 | 查看 build 是否可部署、是否已有相同 promotion 或工作仍在執行 |
| Bundle 部分失敗 | 查看每個元件的 status、failed stage、error code 與 cancel reason；成功元件不必重跑 |
| 部署成功但版本未發布 | Deployment 與 Publish 是不同步驟，需另送發布請求 |
| Tag 衝突 | 確認版本對應的 commit，不能用同名 tag 發布其他 commit |
| 排程未執行 | 確認背景 worker、時區、到期時間、排程狀態及 log |
| 排程顯示已觸發 | 再查看關聯的 deployment / Bundle；觸發成功不代表部署已完成 |
| 通知未寄出 | 檢查 SMTP、收件人及 outbox 的 attempts / last_error / next_attempt_at |
| 附件上傳失敗 | 檢查大小上限與 multipart 欄位，詳見 [API 指南](api-guide.md) |

流程圖未更新時，使用 Refresh 並檢查事件 API。Promotion 查 `/releases/{id}/events` 或
`/deployments/{id}/events`；舊版核准才使用 `/releases/{id}/workflow`，路徑前綴為 `/api/v1`。
不要直接修改 workflow state JSON 或資料庫狀態。

## Drone / Portainer

- **沒有 build**：確認 repository 已在 Drone 啟用、webhook 成功且 branch / event 符合 trigger。
- **Pipeline pending**：確認 Docker runner 在線、資源與 image registry 可用。
- **缺少 secret／版本衝突**：依 [CI/CD](cicd.md) 檢查 secrets 與版本來源。
- **Portainer GET 顯示 connection failed**：尚未取得 HTTP 回應，先看括號內的原因：
  `connection timed out` 查路由、防火牆與 proxy；`connection refused` 查監聽埠與 Docker port mapping；
  `DNS lookup failed` 查容器 DNS；TLS 類錯誤則核對 HTTP／HTTPS 與憑證。
  使用者電腦、NAS 主機、Runner 容器及實際 pipeline step 的網路可能不同，Runner 與 Portainer
  在同一台 NAS 也不能保證建置容器可以連通。先在 NAS 測一般 Docker bridge（不帶 token、不部署）：

  ```sh
  docker run --rm --network bridge curlimages/curl:latest --noproxy '*' \
    --connect-timeout 5 --max-time 10 -sS -o /dev/null -w 'HTTP %{http_code}\n' \
    http://<NAS-IP>:9000/api/status
  ```

  HTTP 200 表示該測試容器可達狀態 API；若仍 timeout，先處理 NAS 的容器路由／防火牆。
  一般 bridge 成功但 CI 失敗時，再檢查實際建置網路與該 step 的 `HTTP_PROXY`、`HTTPS_PROXY`、
  `NO_PROXY` 設定；Python urllib 會採用 proxy 環境設定，curl 的 `--noproxy '*'` 測試則會繞過它。
  若採 `http://portainer:9000` 的容器名稱直連，Portainer 與 pipeline step 必須在同一個
  user-defined network。Runner 可用 [`DRONE_RUNNER_NETWORKS`](https://docs.drone.io/runner/docker/configuration/reference/drone-runner-networks/)
  將指定網路附加到每個 step；只把 Runner 本身接到該網路並不等於建置容器也已接入。
- **Portainer API 回傳 404**：目前使用的 `PUT /api/stacks/{id}/git/redeploy` 是
  [官方支援的路徑](https://github.com/portainer/portainer/blob/2.21.5/api/http/handler/stacks/stack_update_git_redeploy.go)。
  找不到 Stack 或其 Environment 都可能回傳 404，不代表必須更換 API。
  新的部署 log 會先標示 GET Environment、GET Stack，再標示 PUT redeploy：
  GET Environment 失敗先核對 `portainer_endpoint_id`；GET Stack 失敗核對 `portainer_stack_id`；
  兩個 GET 成功後 PUT 才失敗，則依回傳 message 與該版 Portainer API 文件查詢。
  若回應不是 JSON 或出現 redirect，核對 `portainer_url`、reverse proxy 的 path prefix 與 method routing。
  URL 不可包含 UI 的 `#!/...` 路徑；helper 會自動處理結尾 `/api` 與 `/`。
  可在 Portainer UI 的 Stack 詳細頁核對 Stack ID、Environment ID，或以同一 API key
  查詢 GET `/api/stacks` 與 GET `/api/endpoints`；不要把完整 JSON、token 或 Stack Env 貼到公開 log。
- **Portainer build 找不到 Dockerfile**：Stack 的 Compose path 應為 `compose.portainer.yml`。
- **Git redeploy PUT 回傳 502／503／504**：部署結果尚未確認，Portainer 可能仍在 pull、build
  或替換容器。不要只因非 JSON 回應就判定 URL 錯誤，也不要立即重送 PUT。
  [504 表示 gateway 等待上游回應逾時](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/504)；
  helper 的 PUT 等待上限為 1800 秒，無法延長另一層反向代理自己的 timeout。
  在 NAS 先看 `docker logs --since 15m --tail 200 portainer`、Stack 的部署紀錄及實際容器狀態，
  核對預期版本是否上線；舊容器仍健康不等於新部署完成。確認本次工作終止或完成後才決定是否重試。
  若 Drone 使用 Synology HTTPS 反向代理，可將 `portainer_url` 改為 runner 能存取的 Portainer
  內網直連網址，例如 HTTP 已啟用並發布 9000 時的 `http://<NAS-IP>:9000`；瀏覽器仍可使用
  原本的 HTTPS 網域。內網直連需要從 Drone 建置容器的網路驗證，主機能連通不保證容器也能連。
  若必須保留反向代理，依實際建置時間調整代理的 upstream read timeout，並查代理與 Portainer log。
  helper 不會自動重送或把 504 當成功，CI 保持失敗，因此不會接續健康檢查與建立 release tag。
- **Portainer 無法 pull／build**：檢查 Stack 的 Git 憑證、`deploy` 分支、DNS、CA、npm / PyPI 存取。
- **找不到 `stack.env`**：檢查 Portainer Stack 的應用程式環境設定與檔案是否可供 Compose 載入。
- **服務已啟動但 CI health 失敗**：確認 `.drone.yml` 的檢查 URL 與主機埠相符，且 runner 可達。

目前不會自動回滾；Portainer 回應成功也不等於應用程式已健康。

## 容器與資料目錄

在 Docker 主機執行，或使用 Portainer 的容器頁面：

```bash
docker ps -a --filter name=release-controller
docker logs --tail 200 release-controller
docker inspect release-controller --format '{{json .Mounts}}'
curl -fsS http://127.0.0.1:3100/health
```

啟動即退出時，優先檢查 `APP_SECRET_KEY`、OAuth / auth 設定、migration 與 `/data` 權限。
確認 `DATABASE_URL=sqlite:////data/release.db`、實際 `DATA_DIR`，不要用 `chmod -R 777` 排除權限問題。

只能從主機存取時，檢查 `HOST_IP`、防火牆與 reverse proxy。Loopback 綁定不會讓其他主機直接連入。

## 回報問題

提供 commit / 版本、失敗的 pipeline step、HTTP status、紀錄 ID 與相關時間範圍的 log。
先去除 token、cookie、個資與敏感內容；不要附上 `.env` 或完整正式資料庫。
