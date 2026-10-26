# Gitea / Drone 整合

本頁說明 Release Controller 如何部署下游專案。本專案自身的 Portainer 部署另見 [CI/CD](cicd.md)。

## 現行流程

```text
Gitea repository → Drone build
                        ↓
操作者登入 Controller → 選專案、元件、build 與 target
                        ↓
Controller 呼叫 Drone promote → 追蹤 promotion build
                        ↓
部署成功 → 操作者提出發布請求 → Gitea tag / Release
```

- **Gitea** 保存程式碼、提供 OAuth 登入及 Release API。
- **Drone** 負責下游 build、實際部署與該 pipeline 的健康檢查。
- **Controller** 驗證 build、安排順序、呼叫 promotion、追蹤結果與保存事件。

Bundle 依專案元件順序執行，可選任意子集；並非固定 Backend → Frontend。
Controller 不直接登入下游主機，也不執行下游部署 shell。

## 接入一個專案

1. 在 Drone 啟用 repository，讓來源 build 能正常完成。
2. 在下游 repository 設定可接受 promotion event 與預定 target 的部署 pipeline。
3. 在 Controller 的 **Projects → Connections** 建立 Drone / Gitea 連線並測試。
4. 建立專案，設定元件的 Drone / Gitea repository、順序與預設 target。
5. 執行專案驗證，以測試環境的成功 build 驗證單元件部署，再驗證 Bundle。

連線解析順序為元件覆寫 → 專案設定 → 同類型預設連線。
Token 需具有對應 repository 的查詢、promotion 或 Release 建立權限。
Branch 來自 Drone repository metadata 與近期 build history；target 是部署環境，兩者不同。

## 追蹤與失敗

Source build 與 promotion build 分開保存。Controller 依 promotion build 狀態判定部署結果，
下游 pipeline 應將部署及健康檢查失敗回報為 build failure。
啟用背景 worker 後會持續追蹤未完成工作；也可透過 UI / API refresh。

Bundle 某元件失敗會停止後續元件；已成功的元件保留結果。
發布另有獨立狀態，Gitea Release 失敗不會抹掉部署成功紀錄。
呼叫 promotion 或建立 Release 遇到不確定結果時，先查詢歷程及上游狀態，不盲目重送。

## 舊版核准流程與尚未支援項目

舊版 `POST /api/v1/releases` 仍提供 `PENDING → APPROVED → DEPLOYING → SUCCESS/FAILED`
及 `REJECTED` 流程。其 start / finish API 只記錄狀態，沒有接上 Drone promotion。
目前的直接 promotion 與排程也沒有自動串接這個人工核准關卡；不要假設所有部署都必經審核。

Controller 的一般業務 API 受 Gitea session 保護；CI 僅可使用以下獨立 service token
入口。尚無 Gitea webhook 接收／驗章功能。
API 契約見 [API 使用指南](api-guide.md)，權限界線見[安全指南](security.md)。

## CI service API 與 WordPress MCP 自動部署

此入口使用獨立 CI service token；請先完成下節伺服器端設定。人員操作仍使用 Gitea
session。Controller 不建立不存在的專案／元件，也不接受 repository、URL、SHA 或 shell。

```text
main: ci + integration + publish-image
  → publish-image 最後一個 step 通知 Controller（來源 build 此時可以仍為 running）
  → HTTP 202 + request_id（工作已寫入資料庫）
  → worker 等待來源 build 最終 success，驗證來源及必要 stages
  → 共用部署 orchestrator / 去重鎖 → Drone promote(target=production)
  → 追蹤 deploy-production 與健康檢查 → SUCCESS / FAILED
```

`202` 只代表工作已受理，不代表已部署。API 不同步等待來源 build，避免與通知 step 互相等待。
映像建置、Portainer 的 `MCP_TAG`、指定 SHA 部署與 MCP／MinIO 健康檢查仍由下游
`deploy-production` 負責。自動部署不建立 Gitea tag／Release；Publish 保留獨立流程。

### 伺服器端設定、migration 與 token

1. 更新 Controller 映像，備份資料庫後執行 `alembic upgrade head`。
   容器預設啟動命令已會執行；本機可用 `uv run db-upgrade`。
   新 revision 為 `20261025_0018`（接續 `20260926_0017`），新增 CI token、policy、工作、
   事件四張表與 deployment 的 promotion stage snapshot；保留既有部署、session、排程資料。
2. 由管理者在 Projects 設定既有專案 `wordpress-mcp`、元件 `server`，確認元件 Drone
   repository 是 `jhanzihsian/wordpress-mcp-server`、連線指向正確 Drone、target 為
   `production`。CLI 不會建立或更改專案、repository、上游連線。
3. 設定 `BACKGROUND_WORKER_ENABLED=true`。來源等待期限由 `CI_SOURCE_WAIT_SECONDS`
   控制，預設 3600 秒；部署開始後使用既有 `DEPLOYMENT_TIMEOUT_SECONDS`（預設 1800 秒）。
4. 在部署中的 Controller 容器執行下列命令（使用相同資料庫及環境設定）：

```sh
# 每個 component / target 的 policy；其他專案可使用不同的必要 stages。
docker compose exec -T release-controller python -m app.ci_cli set-policy \
  --project wordpress-mcp --component server --target production --branch main \
  --source-stage ci --source-stage integration --source-stage publish-image \
  --promotion-stage deploy-production

# 90 天有效；僅此次 stdout 會輸出完整 token，請在可信終端執行。
docker compose exec -T release-controller python -m app.ci_cli create-token \
  --name wordpress-mcp-drone --project wordpress-mcp --component server \
  --target production --branch main --expires-in-days 90

# 使用上一命令輸出的 id 撤銷，不要把完整 token 傳給 CLI。
docker compose exec -T release-controller python -m app.ci_cli revoke-token --id <TOKEN_ID>
```

本機等價命令為 `uv run python -m app.ci_cli ...`。Portainer 主機也可用
`docker exec release-controller python -m app.ci_cli ...`。建立結果是 JSON：
`id`、`token`、`expires_at`；請保存識別 ID 以供撤銷，完整 token 直接放入 Drone secret。
沒有讀回完整 token 的 API 或 CLI。有效期可指定 1–365 天，預設 90 天。

Policy 以元件與 target 綁定來源分支、必要來源及 promotion stages，至少各一個 stage。
工作受理時保存 policy、repository 與連線的 snapshot；policy 更新套用於後續新工作。
同時，該 component / target 後續的人工及排程部署也保存 promotion stage policy，
共用追蹤器會驗證實際執行的 stages。既有歷史部署不追溯變更。
Token 精確綁定一個 project、component、target、branch，沒有 wildcard 權限。
到期或撤銷會拒絕新請求與狀態查詢；尚未送出 promotion 的 CI 工作也會被拒絕；已採用的人工／排程部署仍使用原操作授權。
已送出的 promotion 會繼續追蹤結果。輪替時可建立同範圍的新 token 查詢舊工作。

### HTTP 契約與查詢

```http
POST /api/v1/ci/deployments
Authorization: Bearer <CI_SERVICE_TOKEN>
Content-Type: application/json

{"project_key":"wordpress-mcp","component_key":"server","source_build_number":123,"target":"production"}
```

成功受理或重複請求皆回 `202`；包含 `request_id`、相對 `status_url`、`status`、
`service_token_id`、`source_build_number`、`commit_sha`、`target`、`deployment_id`、
`promotion_build_number`、`error_code`、`deadline_at` 與 `events`。
等待時 SHA / deployment / promotion 尚未取得，可以是 `null`。
使用相同 Bearer token 呼叫 `GET /api/v1/ci/deployments/{request_id}` 查詢，回 `200`。
查詢會驗證 token 仍有效，且 project、component、target、branch 完全符合該工作。

- `401`：缺少、無效、到期、已撤銷的 token；session cookie 不能替代 CI token。
- `403`：超出 token 資源／來源分支範圍。
- `404`：指定的專案、元件或工作不存在。
- `409`：專案封存、元件停用、CI policy／連線缺漏或 target 設定衝突。
- `422`：格式錯誤、額外欄位、非正整數 build number。

工作狀態為 `WAITING_SOURCE → DEPLOYING → SUCCESS / FAILED`。失敗原因會保存固定
`error_code`，例如 `SOURCE_IDENTITY_REJECTED`、`SOURCE_BUILD_FAILED`、
`SOURCE_STAGES_REJECTED`、`SOURCE_WAIT_TIMEOUT`、`CI_TOKEN_INACTIVE`、
`COMPONENT_CONFIGURATION_CHANGED`、`PROMOTION_STAGES_REJECTED`、`DEPLOYMENT_FAILED`、
`PROMOTION_VERIFICATION_TIMEOUT`（採用已完成部署後，無法在部署期限內驗證 stage）。
Drone 暫時無法連線會保留工作等待，直到來源等待／部署期限；錯誤文字不帶上游原始回應。
部署的完整 promotion 與失敗事件可由人員登入後查看既有 deployments timeline。

### 下游 `.drone.yml` 通知 step（可複製）

在 **publish-image pipeline** 的成功映像推送 step 後加入；使用既有 pipeline 的執行順序。
若該 pipeline 使用 `depends_on` 平行 DAG，請讓此 step 依賴實際的映像推送 step 名稱。
不要讓通知 step 等待 Controller 的部署結果，也不要在 shell 加入 `set -x` 或輸出環境變數。

在下游 `jhanzihsian/wordpress-mcp-server` 的 Drone repository 建立：

| Drone secret | 值 |
| --- | --- |
| `release_controller_url` | `https://controller.example.com`（只填 origin） |
| `release_controller_ci_token` | `create-token` 這次顯示的完整 `rc_ci_...` token |

```yaml
- name: notify-release-controller
  image: python:3.12-alpine
  environment:
    RELEASE_CONTROLLER_URL:
      from_secret: release_controller_url
    RELEASE_CONTROLLER_CI_TOKEN:
      from_secret: release_controller_ci_token
  when:
    event: [push]
    branch: [main]
    status: [success]
  commands:
    - |
      python - <<'PY'
      import json
      import os
      import signal
      import sys
      import urllib.parse
      import urllib.request
      import uuid

      class NoRedirect(urllib.request.HTTPRedirectHandler):
          def redirect_request(self, req, fp, code, msg, headers, newurl):
              return None

      def timeout(signum, frame):
          raise TimeoutError()

      signal.signal(signal.SIGALRM, timeout)
      signal.alarm(25)  # 整體期限；每次連線／socket 操作另限 10 秒。
      try:
          base = os.environ['RELEASE_CONTROLLER_URL'].rstrip('/')
          parsed = urllib.parse.urlsplit(base)
          if (parsed.scheme != 'https' or not parsed.netloc or parsed.path
                  or parsed.username or parsed.password or parsed.query or parsed.fragment):
              raise ValueError()
          token = os.environ['RELEASE_CONTROLLER_CI_TOKEN']
          number = int(os.environ['DRONE_BUILD_NUMBER'])
          if not token or number <= 0:
              raise ValueError()
          body = json.dumps({
              'project_key': 'wordpress-mcp',
              'component_key': 'server',
              'source_build_number': number,
              'target': 'production',
          }).encode('utf-8')
          request = urllib.request.Request(
              base + '/api/v1/ci/deployments', data=body, method='POST',
              headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
          )
          opener = urllib.request.build_opener(NoRedirect())
          with opener.open(request, timeout=10) as response:
              if response.status != 202:
                  raise ValueError()
              result = json.loads(response.read(65536))
              request_id = str(uuid.UUID(result['request_id']))
          print('Controller accepted deployment request ' + request_id)
      except Exception:
          # 不輸出 exception、headers、token 或上游 response body。
          print('Controller notification failed', file=sys.stderr)
          sys.exit(1)
      finally:
          signal.alarm(0)
      PY
```

此範例使用 Python 標準函式庫組 JSON、讀取 `DRONE_BUILD_NUMBER` 與送出 HTTP；
沒有 shell 插值，拒絕重新導向以免 Bearer header 被送往其他主機，維持 HTTPS 憑證驗證。
HTTP 錯誤、非 `202`、逾時及格式錯誤均讓通知 step 失敗。網路結果不明時可重送相同
build number；Controller 會回同一工作。

### 驗證與恢復規則

- worker 只透過元件設定的 Drone 連線／repository 讀取 build；驗證 repository metadata、
  build number、event=`push`、branch=`main`、整體 `success` 與 policy 內所有來源 stages
  `success`，才建立共用部署意圖。`running`／`pending`／`blocked` 不可部署。
- promotion build 整體成功之外，每個必要部署 stage 都要存在且成功，包含至少一個實際
  非 `clone` 的 `success` step，其他 steps 只能成功或 skipped；空 stages、錯名、全 skipped 不算部署成功。
- 相同 component / source build / target 的 CI 意圖具有永久唯一索引，失敗後重送也回原工作，
  不自動重試部署。需要重新部署時由人員檢查原因後走既有人工流程。
- 人工或排程先建立相同 repository／connection／build／target 的有效部署時，CI 會採用既有
  部署並驗證結果；所有入口仍由既有 deployment 唯一索引與原子 promotion claim 保護。
- 工作與 deployment link 在 promotion 前同一交易持久化。worker 重啟後可繼續等待；
  中斷中的 polling lease 最多五分鐘後可重新認領。promotion 已認領但結果不明時只查詢
  Drone promotion 歷程，絕不盲目再次 promote；沿用既有 workflow timeout。
- 每筆工作保存 token ID、來源 build、來源 commit、target、deployment link、結果與狀態事件；
  promotion build 與部署事件在既有 deployment 記錄中，查詢 API 會一起回傳 promotion number。
