# 安全指南

## 目前安全邊界

目前版本使用 Gitea OAuth2 Authorization Code + PKCE 驗證操作人員，並以隨機、雜湊保存、
具到期時間的 server-side session 搭配 HttpOnly cookie 保持登入。受保護 API 會拒絕匿名請求，
`approved_by`、`rejected_by` 與 `requested_by` 由已驗證的 Gitea login 寫入，不接受前端偽造。

目前仍沒有細部角色權限、service token 或 webhook 驗章。登入使用者目前擁有相同操作權限，
因此在角色限制完成前仍不應直接暴露到不受信任的網路。

因此目前建議：

- 綁定 VM `127.0.0.1:3100`。
- 經 SSH tunnel 由受信任人員存取。
- 或放在有額外 authentication 的受信任 reverse proxy 後方。

OAuth callback、Gitea 與 Controller 對外入口都必須使用 HTTPS，正式環境維持
`AUTH_COOKIE_SECURE=true`。只有本機 HTTP 開發環境可設成 `false`。

不要直接使用 `-p 0.0.0.0:3100:8000` 暴露到公司網路或公網。

## 為何服務間用 token，而人員登入用 OAuth/OIDC

兩者解決不同問題：

| 使用者 | 建議機制 | 原因 |
| --- | --- | --- |
| Drone、deployment executor | 短效、可撤銷、最小權限 service token | 沒有人可操作瀏覽器登入；容易放入 CI secret 並輪替 |
| Gitea webhook | 每個來源獨立的 HMAC secret | 驗證 payload 來源及完整性，並可防重放 |
| 人員使用前端 | OAuth 2.0 Authorization Code + PKCE，實務上搭配 OIDC | 需要 SSO、身份、群組/角色、登出及 MFA |

OAuth 是授權框架，本身不是單一「token 的替代品」；OAuth 流程成功後，client 通常仍以
access token 呼叫 API。OIDC 在 OAuth 2.0 上加入可驗證的登入身份。對無人值守的 CI，
強迫走互動式 OAuth redirect 並不合適；對人員，永久共用 token 又無法可靠辨識誰核准。

## 建議的權限

- `release:create`：Drone 建立 release。
- `release:approve`：有核准權的人員 approve/reject。
- `deployment:write`：deployment executor start/finish。
- `release:read`：UI、查詢與監控。
- `admin`：設定、補償流程；不給一般 CI。

`approved_by` 與 `rejected_by` 應從已驗證的人員 identity 寫入，不能接受 client 自由填寫。
每次狀態變更應保存 actor subject、client ID、時間、來源與 correlation ID 的 audit record。

## Service token 要求

- 每個 client、環境各自一把，不共用全域 token。
- Token 只顯示一次，資料庫只保存不可逆 hash 或採可信 identity provider 驗證。
- 設定 expiration、rotation、revocation 與 last-used 追蹤。
- 只透過 HTTPS 傳送；放在 `Authorization: Bearer ...`，不得出現在 URL query。
- Drone 使用 secret store 注入，command 不可 `set -x`，log 需遮罩。
- production token 不能操作其他 environment。

若基礎設施支援 workload identity 或 OAuth 2.0 client credentials，可用短效 access token 取代
長效靜態 token；這仍屬服務身份，不是人員的瀏覽器登入流程。

## Webhook 安全

- 使用足夠長度的隨機 HMAC secret。
- 驗證 signature 前保留原始 body bytes。
- 拒絕過舊 timestamp，記錄並拒絕重複 delivery ID。
- 設定 request body size limit、timeout 與 rate limit。
- Repository、event 與 branch 使用 allowlist；不要相信 payload 內任意 URL。

## 主機與資料

- VM、Gitea、reverse proxy 與 client 之間使用 TLS；內部 CA 也應正確驗證。
- `/data` 與備份限制為部署帳號可讀，備份離開 VM 時加密。
- 不把 `.env`、token、SSH private key、database 或 log dump commit 到 Git。
- Container 使用 rootless Podman；定期更新 Python/Node base image 與依賴。
- 限制 image/source 的寫入者，正式部署以受保護 tag/commit 為準。
- Log 不記錄 Authorization header、secret、完整個資或敏感 deployment output。

## 上線前最低檢核

- [ ] API 不直接暴露在無保護網路。
- [x] 人員登入可由 Gitea 驗證，actor 欄位不能由 client 自由填寫。
- [ ] approve/reject 有角色限制。
- [ ] Drone 與 executor 使用不同且最小權限的身份。
- [ ] Gitea webhook 有 signature、timestamp 與 replay 檢查。
- [ ] TLS、secret rotation、撤銷流程已演練。
- [ ] 狀態變更 audit log 可追溯且不可由一般使用者修改。
- [ ] 備份已加密並做過還原演練。
- [ ] 錯誤與 log 不洩漏 secret。

目前已完成 authentication 與基本 actor 綁定，尚未滿足角色授權與完整 audit context；
完成前應維持受信任網路或額外的 reverse-proxy 存取限制。
