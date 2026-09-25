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
- **Portainer build 找不到 Dockerfile**：Stack 的 Compose path 應為 `compose.portainer.yml`。
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
