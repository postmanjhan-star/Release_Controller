# 安全與權限

## 已實作的保護

- Gitea OAuth2 Authorization Code + PKCE 登入。
- 具到期時間的 server-side session，資料庫保存 token hash，瀏覽器使用 HttpOnly cookie。
- 啟用登入後，業務 API 拒絕匿名操作；核准、拒絕及部署請求的 actor 取自驗證身分。
- Drone / Gitea 連線 token 以 `APP_SECRET_KEY` 加密保存。
- `PROJECT_ADMIN_WRITES=true` 可限制專案與連線寫入必須由 Gitea 管理員執行。
- Workflow events 保存操作歷程與 actor；不是完整的外部稽核或防竄改系統。

## 目前限制

尚無細部角色權限、service token、Gitea webhook 接收及驗章。
除了選用的專案／連線管理員限制，release、promotion、核准、排程及發布等操作未細分權限。
登入驗證也不代表每次部署都經人工核准；舊版核准與直接 promotion 是獨立流程。

部署應使用受信任網路或具有額外存取限制的 HTTPS reverse proxy。
正式環境 `APP_ENV=production` 不允許停用登入，也不允許空白 `APP_SECRET_KEY`。

## 設定重點

| 項目 | 設定方式 |
| --- | --- |
| 人員登入 | `AUTH_ENABLED=true`，設定 Gitea OAuth client 與完全一致的 callback URL |
| Session cookie | HTTPS 入口設定 `AUTH_COOKIE_SECURE=true`；本機 HTTP 才使用 `false` |
| 管理員限制 | 先透過 `/api/v1/auth/session` 確認 `user.is_admin`，再開啟 `PROJECT_ADMIN_WRITES` |
| 連線加密 | 保存原 `APP_SECRET_KEY`；輪替 key 後需重新輸入連線 token |
| SMTP | 正式環境開啟 TLS 驗證；內部 CA 透過 `SMTP_TLS_CA_FILE` 提供 |

Gitea 管理員身分保存於登入 session，權限調整後應重新登入。
將上游 token、OAuth secret 與 Portainer API key 放在相應的 secret / Stack 設定中，
不寫入 repository、URL query 或 log。

## 部署與資料

Compose 的綁定位址預設為 loopback，依實際 proxy / runner 網路配置調整。
目前 `.drone.yml` 對 Portainer 的請求使用 `curl -k`，會略過 TLS 憑證驗證；
應在配置受信任 CA 後移除此選項。這不影響應用程式自身的 SMTP TLS 設定。

`/data` 與備份含部署歷程、登入資料及附件，應限制存取並採取適當的備份保護。
資料庫備份與 `APP_SECRET_KEY` 分開保存，還原方式見[更新與維運](operations.md)。

如果後續要加入 CI service token、webhook 或角色權限，需要先實作驗證與授權機制；
目前不能直接以 `Authorization: Bearer ...` 呼叫 Controller 作為服務登入。
