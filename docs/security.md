# 安全與權限

## 已實作的保護

- Gitea OAuth2 Authorization Code + PKCE 登入。
- 具到期時間的 server-side session，資料庫保存 token hash，瀏覽器使用 HttpOnly cookie。
- 啟用登入後，業務 API 拒絕匿名操作；核准、拒絕及部署請求的 actor 取自驗證身分。
- Drone / Gitea 連線 token 以 `APP_SECRET_KEY` 加密保存。
- `PROJECT_ADMIN_WRITES=true` 可限制專案與連線寫入必須由 Gitea 管理員執行。
- Workflow events 保存操作歷程與 actor；不是完整的外部稽核或防竄改系統。

## 目前限制

尚無一般使用者的細部角色權限、Gitea webhook 接收及驗章。CI service token 僅適用於專用 CI API。
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
目前 `.drone.yml` 對 Portainer 的請求使用 `--insecure`，會略過 TLS 憑證驗證；
應在配置受信任 CA 後移除此選項。這不影響應用程式自身的 SMTP TLS 設定。

`/data` 與備份含部署歷程、登入資料及附件，應限制存取並採取適當的備份保護。
資料庫備份與 `APP_SECRET_KEY` 分開保存，還原方式見[更新與維運](operations.md)。

## CI service token 權限界線

CI 使用獨立的 `ci_service_tokens`，以 48 bytes 隨機資料產生高熵 `rc_ci_...` token；
資料庫只保存 SHA-256 hash。伺服器端 CLI 建立時只輸出一次完整 token，沒有讀回功能。
每個 token 有精確的 project ID、component ID、target、來源 branch、到期日及撤銷時間。
建立、policy 設定與撤銷僅提供伺服器端 CLI，需具備 Controller 容器／資料庫操作權限，
沒有公開 token 管理 API。操作命令見 [CI 接入說明](ci-integration.md#伺服器端設定migration-與-token)。

`POST /api/v1/ci/deployments` 及 `GET /api/v1/ci/deployments/{request_id}` 每次都驗證
Bearer token 有效期、撤銷狀態與精確資源範圍，即使開發環境 `AUTH_ENABLED=false` 亦然。
狀態查詢包含 branch scope；輪替後同範圍的新 token 可追蹤舊工作。
CI token 不能建立人員 session、讀取其他專案、管理專案／連線、操作排程、Publish 或一般部署 API。
既有 Gitea session 的權限與 `PROJECT_ADMIN_WRITES` 行為保持原樣；CI bearer 不會被當作 session。

來源 branch 及必要 stages 是伺服器端 CI policy，CI 呼叫端無法覆寫。repository、連線與 SHA
由 registry／Drone 解析，不接受任意 URL、repository、部署程式或 shell。停用的元件、封存的
專案與未設定 policy 會被拒絕，不會由 CI 自動建立。受理後更換元件 repository／連線會讓
尚未部署的工作失敗；送出 promotion 前共用追蹤器亦再檢查 token、期限、元件與連線。
撤銷不能取消已被 Drone 接收的部署；Controller 會繼續追蹤其結果。

完整 token 只放在 Drone secret `release_controller_ci_token`；URL 放在
`release_controller_url`。不要將 token 放在 URL query、CLI 參數、repository、shell trace、
HTTP debug header dump 或 reverse proxy access log。接入範例拒絕重新導向，防止洩漏 Bearer。
CLI 首次輸出屬機密，請在可信終端直接存入 secret manager；操作歷程只保存 token 識別 ID。
CI API 格式錯誤不回顯輸入、非預期錯誤只回固定訊息；worker 及部署事件使用安全固定錯誤
訊息，不保存上游 exception／response body。Proxy／Drone runner 也須禁止記錄 Authorization header。

CI 工作與事件使用資料庫持久化，與既有部署事件互相連結，但不是獨立的外部防竄改稽核服務。
Token 輪替不需更換 `APP_SECRET_KEY`；備份仍應受保護，因為包含上游加密憑證與部署歷程。

## 文件、診斷與日誌的隱私

文件與 CI 範例只使用範例網域、`<CONTROLLER_URL>` 或 secret／環境變數，不放入真實 DDNS。
專案協作規範見 `agent.md`；測試會掃描公開文件與設定範例，失敗時只列出檔案及行號。
真實連線資訊保留在 Git 忽略的私人設定與受保護資料庫，不透過文件公開。

API 與 MCP 共用日誌遮蔽，涵蓋最終格式化的訊息、參數、例外堆疊與 Uvicorn／HTTP client
日誌；遮蔽 HTTP(S) URL、常見 DDNS hostname、Authorization／Cookie／API key、登入 callback
code／state 及已設定的服務密鑰。MCP stdio 模式繼續將日誌送到 stderr。
排程與 SMTP 失敗記錄使用固定診斷訊息；Portainer CI helper 僅輸出允許的固定錯誤文字，
避免上游把私人網址或密鑰放進錯誤訊息後被再次輸出。
此保護不會移除具權限使用者在管理介面操作所需的連線設定；外部 reverse proxy 與 runner
仍需個別禁止記錄 secret、Authorization header 與原始環境變數。
