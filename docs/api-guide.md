# API 使用指南

Base path 為 `/api/v1`。完整欄位與回應以執行中服務的 `/docs`、`/openapi.json` 為準。
本頁範例的 project ID、component key 與 build number 必須換成實際資料。

## 登入與權限

- `GET /api/v1/auth/login`：開始 Gitea OAuth 登入。
- `GET /api/v1/auth/session`：查看登入狀態與 `user.is_admin`。
- `POST /api/v1/auth/logout`：登出。
- `/health` 不需登入；其餘業務 API 在 `AUTH_ENABLED=true` 時需要有效的 session cookie。
- `PROJECT_ADMIN_WRITES=true` 時，專案與連線的寫入另要求 Gitea 管理員。

登入後可在同一瀏覽器使用 `/docs`。尚無 Bearer service token 登入方式。
本機 curl 測試可使用 `APP_ENV=development`、`AUTH_ENABLED=false`；正式環境不可停用驗證。
啟用驗證時，核准、拒絕與部署請求的操作者由 session 決定，不能藉由 body 偽造。

## 常用端點

下表路徑均省略 `/api/v1`：

| 方法與路徑 | 用途 |
| --- | --- |
| `GET /projects`、`POST /projects` | 列出、建立專案 |
| `GET /projects/{project_ref}`、`PATCH /projects/{project_ref}` | 查看、修改專案 |
| `POST /projects/{project_ref}/validate` | 驗證專案的上游設定 |
| `GET /connections`、`POST /connections` | 列出、建立連線 |
| `POST /connections/{connection_id}/test` | 測試連線 |
| `GET /projects/{project_ref}/components/{component_ref}/builds` | 查詢元件的 repository、branches 與 builds |
| `POST /projects/{project_ref}/components/{component_ref}/builds/{build_number}/promote` | 單元件部署 |
| `POST /releases/promote` | 依專案順序部署所選元件 |
| `GET /releases`、`GET /deployments`、`GET /publishes` | 查詢歷程 |
| `GET /releases/{id}`、`GET /deployments/{id}` | 查詢單筆結果 |
| `GET /releases/{id}/events`、`GET /deployments/{id}/events` | 查詢 promotion 流程事件 |
| `POST /releases/{id}/refresh`、`POST /deployments/{id}/refresh` | 追蹤並推進部署 |
| `POST /releases/{id}/retry` | 重試 Bundle 尚未成功的元件 |
| `POST /releases/{id}/publish`、`POST /deployments/{id}/publish` | 建立 Gitea Release |
| `POST /schedules`、`GET /schedules`、`GET /schedules/{id}` | 建立、查詢排程 |
| `DELETE /schedules/{id}` | 取消尚未執行的排程 |
| `GET /notifications/recipients`、`POST /notifications/recipients` | 管理通知收件人 |
| `GET /notifications/outbox` | 查詢通知投遞狀態 |

`project_ref` 可使用專案 ID 或 key，`component_ref` 可使用元件 ID 或 key。
各清單的篩選與分頁參數不完全相同，請查 `/docs`，不要假設所有端點接受相同欄位。

## 部署

單元件 promotion 的 JSON body：

```json
{"target": "pre-production"}
```

Bundle 使用 `POST /api/v1/releases/promote`：

```json
{
  "project_id": "<project-id>",
  "components": [
    {"key": "api", "build_number": 456},
    {"key": "web", "build_number": 123}
  ],
  "target": "pre-production"
}
```

各元件的 build number 獨立。執行順序由專案設定決定；未指定 target 時使用專案預設值。
若元件設定 `promote_target_override`，該元件會優先使用自己的目標。
查詢結果時分別看部署 `status`、`publish_status`、`current_stage`、`error_code` 與
`cancel_reason`，並保留回傳的 ID 供後續追蹤。

## 發布版本

部署成功後，對 Bundle 或單元件的 `/publish` 端點送出：

```json
{
  "version": "v1.2.3",
  "name": "Release 1.2.3",
  "release_notes": "本次更新內容",
  "draft": false,
  "prerelease": false
}
```

部署與發布是不同狀態。Tag 已指向其他 commit 時會衝突；重試前先查詢既有發布結果。
設為不需發布的元件會被略過。

## 排程與附件

`POST /api/v1/schedules`：

```json
{
  "project_id": "<project-id>",
  "components": [{"key": "api", "build_number": 456}],
  "target": "production",
  "scheduled_for": "2030-01-15T09:30:00",
  "timezone": "Asia/Taipei",
  "release_version": "1.2.3",
  "release_notes": "本次更新內容",
  "notification_recipients": ["release-team@example.com"]
}
```

日期請換成預定執行時間。沒有 offset 的時間依 `timezone` 解讀，資料庫保存 UTC。
使用 `components` 時，mode 依元件數推導為 `SINGLE` 或 `BUNDLE`；production 排程需填版本與更新內容。
排程中的版本／備註用於紀錄與通知，不等同呼叫 Gitea `/publish`。

單元件、Bundle promotion 與建立排程均接受 JSON，或帶 `payload`（JSON 字串）及
`attachment`（檔案）的 `multipart/form-data`。附件預設上限 10 MiB，隨通知保存於資料庫。

背景 worker 負責到期執行及通知。`POST /schedules/run-due`、
`POST /notifications/dispatch` 會實際觸發工作，僅在需要手動執行時使用。

## 舊版核准 API

`POST /releases` 建立舊版核准紀錄，body 為 repository、branch、commit_sha、environment 與可選 message。
其流程仍為：

```text
PENDING → APPROVED → DEPLOYING → SUCCESS / FAILED
        ↘ REJECTED
```

動作端點是 `/{id}/approve`、`/{id}/reject`、`/{id}/deployment/start`、`/{id}/deployment/finish`，
前綴均為 `/releases`。Approve / reject 的 body 可送 `{}`；finish 必須指定
`{"status":"SUCCESS"}` 或 `{"status":"FAILED"}`，可附加 message。
這組 start / finish 僅更新紀錄，不會執行 Drone promotion。
`GET /releases/{id}/workflow` 用於這類核准紀錄；promotion 流程請查 `/events`。

舊版 frontend / backend build 欄位與 `/drone/{component}/builds` 端點為相容用途，
新整合使用上述 project / component 路徑與 `components` body。

## 常見錯誤

| HTTP | 檢查方向 |
| --- | --- |
| `401` / `403` | Session 過期或管理員限制 |
| `404` | 專案、元件、build 或紀錄不存在 |
| `409` | 重複 promotion、狀態衝突或版本衝突 |
| `413` / `422` | 附件過大或 payload 不合法 |
| `502` / `503` / `504` | 上游失敗、設定不可用或逾時；以 detail / error code 判斷 |
| `500` | 未預期錯誤，查詢服務 log |

回報問題時保留 HTTP status、去除敏感資訊的 response body 與紀錄 ID。
