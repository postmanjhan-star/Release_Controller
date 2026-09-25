# 系統架構

## 執行流程

```text
React / TypeScript UI
        ↓ REST / session cookie
FastAPI ── OAuth ── Gitea
        ├─ 專案、元件與加密連線
        ├─ Promotion orchestration ── Drone API
        ├─ Publish orchestration ── Gitea Release API
        ├─ 舊版核准流程 ── SpiffWorkflow
        └─ 背景 worker ── 排程、復原、通知 outbox ── SMTP
                        ↓
                 SQLAlchemy / SQLite
```

每個專案可設定任意數量的元件，各元件有 repository、部署順序、target 與連線設定。
單元件 promotion 與 Bundle 共用部署邏輯；Bundle 按專案順序執行所選元件，並非固定兩個 repository。

Source build 用來選定來源 commit，promotion build 用來追蹤實際部署。
部署與 Gitea Release 發布分別保存狀態，發布失敗不覆寫部署結果。

## 程式分層

| 路徑 | 責任 |
| --- | --- |
| `app/api/`、`app/schemas/` | HTTP 路由、請求驗證與回應 |
| `app/domain/` | 領域實體、狀態及 repository / gateway 介面 |
| `app/usecase/` | 已抽出的 release、registry 與 orchestration 用例 |
| `app/infrastructure/` | 依賴注入、SQLite repository 與上游 adapter |
| `app/services/` | 部署、發布、排程、通知、驗證及既有流程協調 |
| `app/integrations/` | Drone / Gitea HTTP client 與重試處理 |
| `app/db/`、`alembic/` | SQLAlchemy models、session 與 migration |
| `frontend/src/` | React shell、登入狀態、API client、controller 與 BPMN viewer |

後端正逐步分層，仍有 service 直接協調 SQLAlchemy 與外部服務；不要把歷史 DDD 審查中的目標
架構視為全部已完成。依賴組裝集中在 `app/infrastructure/di/injection.py`。

## 主要資料

| 資料表 | 內容 |
| --- | --- |
| `projects`、`project_components`、`upstream_connections` | 專案、元件與加密的上游連線 |
| `deployments`、`release_bundles` | 單元件與 Bundle 部署狀態 |
| `publish_records`、`workflow_events` | 發布結果與流程事件 |
| `deployment_schedules`、`schedule_component_builds` | 排程時間及各元件的來源 build |
| `email_outbox`、`email_notification_cursors`、`notification_recipients` | 通知投遞、事件進度與共用名單 |
| `auth_sessions`、`oauth_login_attempts` | 登入 session 與 OAuth 流程 |
| `releases`、`release_workflows` | 保留的舊版核准紀錄與 SpiffWorkflow 狀態 |

排程時間以 UTC 保存，另保留 IANA 時區。通知附件也存於 SQLite，資料庫備份包含附件。
連線 token 需配合原 `APP_SECRET_KEY` 才能解密。

## Workflow 與背景工作

舊版人工核准由 SpiffWorkflow 執行，狀態及序列化流程一同保存。
目前的 promotion / publish 以服務端狀態與 append-only events 追蹤；UI 顯示 stage timeline
及 BPMN 標記，不執行部署邏輯。

`app/workflows/bpmn/` 保留核准、frontend / backend、Bundle 與排程定義。
目前 `/workflows/{mode}/definition` 提供固定模式的 XML，並非任意專案的動態 BPMN 產生器。
多元件部署的實際內容應讀取 deployment / Bundle 明細及事件。
變更既有 BPMN element ID 前，需考慮序列化流程與前端 marker 的相容性。

背景 worker 處理到期排程、未完成部署的持續追蹤與中斷復原，再處理通知 outbox。
SMTP 投遞與部署 transaction 分離，通知失敗不回滾部署；outbox 具去重、claim timeout 與重試。

## 部署邊界

`Containerfile` 建置前端至 `app/static`，由 FastAPI 一併提供。
容器啟動先執行 `alembic upgrade head`，再以單一 Uvicorn worker 監聽 `8000`。
資料庫為 `/data/release.db`，使用持久化掛載；目前 SQLite 部署維持單一服務 instance。

Controller 自身由 Drone + Portainer 部署；下游元件由各自 Drone pipeline 部署。
`/health` 只檢查本服務與資料庫，Connections / 專案驗證才檢查實際上游設定。
登入與權限限制見[安全指南](security.md)，備份見[更新與維運](operations.md)。
