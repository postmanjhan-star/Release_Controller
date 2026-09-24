# 系統架構

## 元件

```text
Browser
  |  HTML/JS, REST
  v
FastAPI (Uvicorn, one process in container)
  |-- StaticFiles -> app/static
  |-- ReleaseOrchestrator -> Backend-first workflow
  |-- DroneBuildService -> Drone API (two independent repos)
  |-- DeploymentService -> SQLAlchemy -> SQLite
  +-- Legacy ReleaseService/WorkflowService (preserved v1)
  +-- BackgroundWorker -> persisted schedules + email outbox

Drone Frontend Repo          Drone Backend Repo
  +-- independent promote      +-- independent promote
```

- **FastAPI** 提供 UI、release API、workflow API 與 health endpoint。
- **SQLAlchemy/Alembic** 管理資料存取與 schema migration。
- **Drone API client** 查詢、驗證與 promote 兩個獨立 repository。
- **Release Orchestrator** 先部署 Backend，再依結果啟動或取消 Frontend。
- **Workflow events** 保存 current/failed/cancelled stage 的 append-only audit。
- **bpmn-js Viewer** 載入 machine-readable BPMN-DI，並以 workflow events 疊加即時執行狀態。
- **SpiffWorkflow** 仍執行並保存既有 v1 approval workflow。
- **SQLite WAL** 支援目前的單機、小量並行使用方式。

## Release 與 BPMN 的關係

建立 release 的同一個 transaction 會建立 workflow instance。業務狀態與 BPMN task
在服務層一起推進：

| Release 動作 | Release 狀態 | SpiffWorkflow 動作 |
| --- | --- | --- |
| create | `PENDING` | 建立 instance，等待 `Task_approval_gate` |
| approve | `APPROVED` | 完成 approval，decision=`approved` |
| reject | `REJECTED` | 完成 approval，decision=`rejected`，到結束事件 |
| deployment/start | `DEPLOYING` | 完成 `Task_start_deployment` |
| deployment/finish | `SUCCESS`/`FAILED` | 完成 deployment 並走到對應結束事件 |

Release 狀態是 API 行為判斷的主要業務狀態；序列化的 Spiff workflow 是流程圖執行
細節。兩者都儲存在同一個 SQLite transaction 中，避免只更新其中一側。

舊資料若沒有 workflow，第一次查詢或轉移時會依 release status 補建相符的 instance。

## 資料模型

### v2.2 orchestration tables

- `release_bundles`：bundle status、workflow instance、current/failed stage 與 safe error。
- `deployments`：component、source/promotion build、target、status、error/cancel reason。
- `workflow_events`：append-only stage/event history。

這三表由 `20260821_0003` 直接接在 v1 head 後建立；`releases` 與
`release_workflows` 不刪除、不改寫，因此既有資料與 API 可繼續使用。

### `releases`

- 主鍵：UUID 字串 `id`。
- 識別：`repository`、`branch`、`commit_sha`、`environment`。
- 狀態與時間：`status`、created/updated/approved/rejected/deploy 時間。
- 操作者：`approved_by`、`rejected_by`。
- 備註：`message`。
- 唯一限制：`repository + commit_sha + environment`。

### `release_workflows`

- `release_id` 同時是主鍵與 `releases.id` 外鍵。
- `definition_id` 目前固定為 `release_approval`。
- `state_json` 保存 SpiffWorkflow 完整序列化狀態。
- 刪除 release 時 workflow 會透過 foreign key cascade 刪除。

正式資料庫位於 container 的 `/data/release.db`。SQLite 另可能建立
`release.db-wal` 與 `release.db-shm`，備份時不可只在服務運行中任意複製主檔；請使用
[更新與維運](operations.md)中的 SQLite backup API 做法。

## BPMN 定義

v2.2 定義位於 `app/workflows/bpmn/`：`frontend_only.bpmn`、
`backend_only.bpmn`、`release_bundle.bpmn`。穩定 stage ID 包含
`VALIDATE_FRONTEND`、`VALIDATE_BACKEND`、`PROMOTE_BACKEND`、
`WAIT_BACKEND_DEPLOYMENT`、`PROMOTE_FRONTEND`、`WAIT_FRONTEND_DEPLOYMENT` 與
`COMPLETE_RELEASE`。

既有 v1 定義檔是 `app/workflows/bpmn/release_approval.bpmn`，API 由
`GET /api/v1/workflows/release-definition` 原樣提供。流程的重要 element ID 是：

- `Task_approval_gate`
- `Task_start_deployment`
- `Task_execute_deployment`
- `EndEvent_rejected`
- `EndEvent_success`
- `EndEvent_failed`

修改 element ID 會影響 Python workflow service 與既有序列化 instance。變更 BPMN
前必須設計 instance migration 或版本化 definition，不能只替換 XML。

前端只讀取 BPMN 與 workflow events，不自行推算或改寫後端流程。顏色契約為：綠色
`completed`、橘色 `current`、紅色 `failed`、灰色虛線 `cancelled`；未開始的節點維持白色。
Bundle 與 standalone deployment 在畫面可見時每五秒 refresh，重新套用同一份事件狀態。

### 通知與排程

- `deployment_schedules` 保存 mode、source build、target、UTC 執行時間、原始 IANA
  時區、收件人及觸發結果；背景 worker 到期後仍呼叫既有 `ReleaseOrchestrator`。
- worker 會持續 refresh 由排程啟動的 deployment/bundle，直到終止狀態。
- Email dispatcher 訂閱已持久化的 terminal/failure `workflow_events`，並先寫入
  `email_outbox`，workflow transaction 內不會呼叫 SMTP。
- `workflow_event_id` 唯一限制避免重複建立通知；寄送前以 `PROCESSING` claim 避免
  worker 重複寄送，逾時 claim 可回收，失敗採 exponential retry。
- migration 會建立 event cursor 作為通知起點，升級前的歷史 workflow events 不會在
  SMTP 啟用後被一次補寄。
- 新增穩定 stage ID 後，Viewer 會沿用相同 event-to-marker 映射；修改既有 ID 則需要
  workflow definition versioning 與 migration。

## 目錄

```text
app/
  api/routes/       FastAPI endpoints
  core/             設定與 logging
  db/models/        SQLAlchemy models
  schemas/          Pydantic request/response
  services/         release 與 workflow transaction 邏輯
  workflows/        BPMN definition
  static/           Vite build 產物，由 FastAPI 提供
alembic/             schema migrations
frontend/            bpmn-js/Vite 原始碼與 smoke test
tests/               API、workflow、health、frontend contract tests
Containerfile        Node build stage + Python runtime stage
```

## 啟動與 Container 契約

Container 啟動命令先執行 `alembic upgrade head`，成功後啟動單一 Uvicorn worker：

```text
container port: 8000
health endpoint: /health
database URL: sqlite:////data/release.db
persistent directory: /data
```

單一 container/worker 是目前 SQLite 架構的預期部署。要水平擴充多 instance 時，應先
改用具備並行與共享儲存能力的資料庫，並重新檢視 workflow locking。

## 設計邊界

- Release Controller 記錄部署流程，不負責執行任意 shell command。
- 實際 build/deploy/health check 應由 Drone 或受控 deployment executor 執行。
- 前端目前是操作與監控介面，不是 BPMN editor。
- authentication、authorization、webhook 驗章與 audit log 尚未實作。
