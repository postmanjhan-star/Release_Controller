# API 使用指南

## 部署排程

建立排程時，無時區 offset 的 `scheduled_for` 會依 `timezone`（IANA 名稱）解讀，資料庫
一律保存 UTC。三種 mode 的 build 欄位契約與立即部署相同：

```http
POST /api/v1/schedules
Content-Type: application/json

{
  "mode": "BUNDLE",
  "frontend_build_number": 123,
  "backend_build_number": 456,
  "target": "pre-production",
  "scheduled_for": "2026-08-26T09:30:00",
  "timezone": "Asia/Taipei",
  "notification_recipients": ["release-team@example.com"]
}
```

- `GET /api/v1/schedules?status=PENDING`：查詢排程。
- `GET /api/v1/schedules/{id}`：查詢單筆及觸發後的 deployment/bundle id。
- `DELETE /api/v1/schedules/{id}`：取消尚未執行的排程。
- `POST /api/v1/schedules/run-due`：立即掃描到期排程，供維運診斷；正常由背景 worker 執行。
- `GET /api/v1/notifications/outbox`：查詢 SMTP delivery 狀態，不回傳信件本文。
- `POST /api/v1/notifications/dispatch`：立即收集事件並嘗試寄送，供維運診斷。

API base path 為 `/api/v1`。互動式 Swagger UI 位於 `/docs`，完整 schema 位於
`/openapi.json`。目前所有 endpoint **沒有 authentication**；不要直接暴露到公網。

以下以本機為例：

```bash
BASE_URL=http://127.0.0.1:8000
```

Windows PowerShell 使用 `curl.exe` 可避免 `curl` alias 的參數差異。

## Endpoint

| Method | Path | 說明 |
| --- | --- | --- |
| `GET` | `/health` | Process 與資料庫健康檢查 |
| `GET` | `/api/v1/gitea/health` | 呼叫設定的 Gitea `/api/healthz`；無法連線時回 `503` |
| `POST` | `/api/v1/releases` | 建立 `PENDING` release |
| `GET` | `/api/v1/releases` | 查詢 release 清單 |
| `GET` | `/api/v1/releases/{id}` | 查詢單筆 release |
| `POST` | `/api/v1/releases/{id}/approve` | 核准 `PENDING` release |
| `POST` | `/api/v1/releases/{id}/reject` | 拒絕 `PENDING` release |
| `POST` | `/api/v1/releases/{id}/deployment/start` | 將 `APPROVED` 改為 `DEPLOYING` |
| `POST` | `/api/v1/releases/{id}/deployment/finish` | 將 `DEPLOYING` 結束為成功或失敗 |
| `GET` | `/api/v1/releases/{id}/workflow` | 取得該筆 BPMN 執行狀態 |
| `GET` | `/api/v1/workflows/release-definition` | 取得 BPMN XML |
| `GET` | `/api/v1/drone/status` | Drone upstream 狀態（不影響 `/health`） |
| `GET` | `/api/v1/drone/config` | 前端可讀的預設 promotion target |
| `GET` | `/api/v1/drone/{frontend|backend}/builds` | 查詢獨立 repository builds |
| `POST` | `/api/v1/drone/{component}/builds/{number}/promote` | 單獨部署一個 component |
| `POST` | `/api/v1/releases/promote` | 建立 Backend → Frontend bundle |
| `POST` | `/api/v1/releases/{id}/refresh` | refresh 並推進 bundle |
| `GET` | `/api/v1/releases/{id}/events` | append-only workflow audit |
| `GET` | `/api/v1/deployments` | 查詢 component deployment history |
| `GET` | `/api/v1/deployments/{id}` | 查詢 deployment 與精確失敗資訊 |
| `GET` | `/api/v1/deployments/{id}/events` | 查詢 standalone deployment workflow events |
| `POST` | `/api/v1/deployments/{id}/refresh` | refresh standalone deployment |
| `GET` | `/api/v1/workflows/{mode}/definition` | 取得 v2.2 BPMN XML |

## v2.2 Drone orchestration

Frontend 與 Backend build number 完全獨立。以下 bundle 會先驗證兩邊 build，兩邊都
通過後才 promote Backend；Frontend 會保持 `WAITING`，直到 Backend 成功：

Build 清單回應同時包含 Drone 從 Gitea 同步的 `repository` metadata、近期 build
history 中實際出現的 `branches`，以及 `items`。branch 不由 env mapping 決定；UI 會讓
Frontend 與 Backend 各自選擇 branch，再顯示該 branch 的 source builds。

```bash
curl -fsS -X POST "$BASE_URL/api/v1/releases/promote" \
  -H "Content-Type: application/json" \
  -d '{
    "frontend_build_number": 123,
    "backend_build_number": 456,
    "target": "pre-production"
  }'
```

UI 或操作者可每五秒 refresh：

```bash
curl -fsS -X POST "$BASE_URL/api/v1/releases/$RELEASE_ID/refresh"
curl -fsS "$BASE_URL/api/v1/releases/$RELEASE_ID/events"
```

Backend 失敗時 Frontend 會成為 `CANCELLED` 並保存 `cancel_reason`；Backend 已成功而
Frontend 失敗時 bundle 為 `PARTIAL_FAILURE`。兩邊都成功才是 `SUCCESS`。

Standalone 例子：

```bash
curl -fsS -X POST \
  "$BASE_URL/api/v1/drone/frontend/builds/123/promote" \
  -H "Content-Type: application/json" \
  -d '{"target":"pre-production"}'
```

Standalone 不會為未參與的另一個 component 建立假 `CANCELLED` record。

## 完整生命週期範例

### 1. 建立 release

```bash
curl -fsS -X POST "$BASE_URL/api/v1/releases" \
  -H "Content-Type: application/json" \
  -d '{
    "repository": "smt-assistant-backend",
    "branch": "pre-production",
    "commit_sha": "7c1fe1ac1d4ef9b5c46279f39db965a8c73389ae",
    "environment": "pre-production",
    "message": "Drone build passed"
  }'
```

成功回傳 HTTP 201。將回應中的 `id` 保存成 `RELEASE_ID`；以下範例使用：

```bash
RELEASE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### 2A. 核准

```bash
curl -fsS -X POST "$BASE_URL/api/v1/releases/$RELEASE_ID/approve" \
  -H "Content-Type: application/json" \
  -d '{"approved_by":"jhan"}'
```

### 2B. 或拒絕

```bash
curl -fsS -X POST "$BASE_URL/api/v1/releases/$RELEASE_ID/reject" \
  -H "Content-Type: application/json" \
  -d '{"rejected_by":"jhan","message":"Deployment postponed"}'
```

拒絕後流程結束；不要再執行以下部署步驟。

### 3. 記錄部署開始

```bash
curl -fsS -X POST \
  "$BASE_URL/api/v1/releases/$RELEASE_ID/deployment/start"
```

這只把控制器內的狀態改為 `DEPLOYING`，不會執行實際部署。

### 4. 實際部署後記錄結果

成功：

```bash
curl -fsS -X POST \
  "$BASE_URL/api/v1/releases/$RELEASE_ID/deployment/finish" \
  -H "Content-Type: application/json" \
  -d '{"status":"SUCCESS","message":"Health check passed"}'
```

失敗：

```bash
curl -fsS -X POST \
  "$BASE_URL/api/v1/releases/$RELEASE_ID/deployment/finish" \
  -H "Content-Type: application/json" \
  -d '{"status":"FAILED","message":"Health check failed"}'
```

`status` 只接受 `SUCCESS` 或 `FAILED`。

## 查詢

單筆 release：

```bash
curl -fsS "$BASE_URL/api/v1/releases/$RELEASE_ID"
```

Workflow：

```bash
curl -fsS "$BASE_URL/api/v1/releases/$RELEASE_ID/workflow"
```

清單可使用 `repository`、`branch`、`environment`、`status`、`limit`、`offset`：

```bash
curl -fsS \
  "$BASE_URL/api/v1/releases?environment=pre-production&status=PENDING&limit=20&offset=0"
```

所有字串篩選都是精確比對；前端搜尋才是載入後的部分文字比對。`limit` 預設 50、
最小 1、最大 100，`offset` 最小 0。清單回應格式：

```json
{
  "items": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

Workflow 回應的重點欄位：

- `is_complete`：整個 BPMN 是否結束。
- `current_element_ids`：現在等待的 BPMN element ID。
- `completed_element_ids`：已走過的 task、event 與 flow ID。
- `steps`：前端右側步驟清單，狀態可能是 `ACTIVE`、`COMPLETED`、`PENDING`、
  `SKIPPED`。

## 狀態轉移

```text
PENDING --approve--> APPROVED --start--> DEPLOYING --finish SUCCESS--> SUCCESS
   |                                      |
   +--reject--> REJECTED                  +--finish FAILED----> FAILED
```

服務會以資料庫條件更新避免同一筆 release 被並行重複轉移。呼叫不符合目前狀態的
動作會收到 HTTP 409。

## 錯誤

| HTTP | 情境 |
| --- | --- |
| `404` | release ID 不存在 |
| `409` | 重複 release，或狀態不允許該動作 |
| `422` | JSON 缺欄位、空字串、長度或 enum 不合法 |
| `500` | 未預期的伺服器錯誤；回應不洩漏 exception 細節 |
| `503` | `/health` 無法查詢資料庫 |

一般錯誤回應為 `{"detail":"..."}`。CI 應同時記錄 HTTP status 與 response body，
且只有在實際部署和 health check 都成功後才送出 `SUCCESS`。
