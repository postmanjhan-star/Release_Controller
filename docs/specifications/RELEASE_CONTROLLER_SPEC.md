# Release Controller FastAPI 規格

> 歷史版本規格：保留原始需求與設計，部分流程與部署方式已變更。
> 目前操作以[文件索引](../README.md)、[API 指南](../api-guide.md)及[部署設定](../deployment.md)為準。

> 目標：建立一個可直接進 Git 版控、可由 `scripts/deploy-release-controller.sh` 在 Gitea VM 上使用 Podman build / run 的 FastAPI 服務。
>
> 本文件是實作規格。Agent 應依此文件完成可執行、可測試、可容器化部署的專案。

---

## 1. 專案定位

專案名稱：

```text
release-controller
```

用途：

```text
Gitea / Drone
      │
      ▼
release-controller
      │
      ├─ Release 紀錄
      ├─ Approval Gate
      ├─ Deployment Status
      └─ SQLite
```

第一版先完成：

1. FastAPI HTTP API
2. SQLite 持久化
3. Release 建立、查詢、列表
4. Approve / Reject 狀態轉換
5. `/health` health check
6. Containerfile
7. Alembic migration
8. pytest 自動化測試

第一版不做：

- SMTP relay
- Email 通知
- Drone API 呼叫
- Gitea webhook
- Production server SSH deployment
- Web UI
- Redis
- Celery / Huey
- PostgreSQL
- 多節點部署

以上功能保留給後續階段。

---

## 2. Runtime / Deployment Contract

### Python

使用：

```text
Python 3.10+
FastAPI
Uvicorn
SQLAlchemy 2.x
Pydantic 2.x
Alembic
SQLite
pytest
```

### Container

Repository root 必須存在：

```text
Containerfile
```

部署腳本會在 repository root 執行：

```bash
podman build \
  -t localhost/release-controller:latest \
  -f Containerfile \
  .
```

Container 必須監聽：

```text
0.0.0.0:8000
```

啟動命令概念：

```bash
alembic upgrade head &&
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1
```

第一版固定使用：

```text
workers = 1
```

目前使用 SQLite，先避免增加不必要的多程序寫入競爭。

### Host deployment

既有部署腳本會使用：

```text
Container name:
release-controller

Image:
localhost/release-controller:latest

Podman network:
release-net

Host:
127.0.0.1:3100

Container:
8000

Volume:
~/services/release-controller/data:/data

Database:
sqlite:////data/release.db
```

因此應用程式不得將 SQLite 寫在 source tree。

正式環境必須使用：

```text
/data/release.db
```

---

## 3. Repository Structure

建議專案結構：

```text
release-controller/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── health.py
│   │       └── releases.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── session.py
│   │   └── models/
│   │       ├── __init__.py
│   │       └── release.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── release.py
│   └── services/
│       ├── __init__.py
│       └── release_service.py
├── alembic/
│   ├── versions/
│   └── env.py
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   └── test_releases.py
├── alembic.ini
├── Containerfile
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

不要把所有 business logic 寫在 `main.py` 或 route function 裡。

---

## 4. Configuration

使用 environment variables。

`app/core/config.py` 使用 `pydantic-settings`。

至少支援：

```env
APP_NAME=release-controller
APP_ENV=production
LOG_LEVEL=INFO
DATABASE_URL=sqlite:////data/release.db
```

`.env.example`：

```env
APP_NAME=release-controller
APP_ENV=development
LOG_LEVEL=INFO
DATABASE_URL=sqlite:///./release.db
```

正式環境的 `DATABASE_URL` 由 Podman deployment script 傳入。

程式碼不得 hard-code：

```text
/home/gitea
127.0.0.1:3100
release-net
```

這些屬於 deployment infrastructure。

---

## 5. Database

正式環境：

```text
sqlite:////data/release.db
```

SQLite connection：

```text
check_same_thread = false
```

建議 connection 初始化套用：

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

---

## 6. Release Domain Model

資料表：

```text
releases
```

欄位：

| Field | Type | Required | 說明 |
|---|---|---:|---|
| id | UUID string | yes | Release ID |
| repository | string | yes | Repository 名稱 |
| branch | string | yes | Git branch |
| commit_sha | string | yes | Git commit SHA |
| environment | string | yes | 例如 pre-production / production |
| status | enum/string | yes | Release 狀態 |
| created_at | datetime | yes | 建立時間 |
| updated_at | datetime | yes | 更新時間 |
| approved_by | string/null | no | 核准人 |
| approved_at | datetime/null | no | 核准時間 |
| rejected_by | string/null | no | 駁回人 |
| rejected_at | datetime/null | no | 駁回時間 |
| deploy_started_at | datetime/null | no | 部署開始 |
| deploy_finished_at | datetime/null | no | 部署結束 |
| message | text/null | no | 備註或錯誤訊息 |

時間一律儲存 UTC，API 使用 ISO 8601 datetime。

### Unique constraint

避免同一 commit 對同一環境重複建立 release：

```text
repository
+ commit_sha
+ environment
```

重複建立：

```http
409 Conflict
```

---

## 7. Release Status

允許狀態：

```text
PENDING
APPROVED
REJECTED
DEPLOYING
SUCCESS
FAILED
```

狀態流程：

```text
             ┌────────── REJECTED
             │
PENDING ─────┤
             │
             └────────── APPROVED
                           │
                           ▼
                       DEPLOYING
                           │
                    ┌──────┴──────┐
                    ▼             ▼
                 SUCCESS        FAILED
```

第一版 API 只需要直接操作：

```text
PENDING -> APPROVED
PENDING -> REJECTED
```

`DEPLOYING / SUCCESS / FAILED` 先建立 domain model 支援，等後續 Drone integration 再加入操作 API。

---

## 8. API Prefix

除了 health endpoint 外，所有 API 使用：

```text
/api/v1
```

---

## 9. Health API

### GET /health

用途：

- Podman deployment health check
- 確認 FastAPI process 存活
- 確認 SQLite 可連線

成功：

```http
HTTP/1.1 200 OK
```

Response：

```json
{
  "status": "ok",
  "service": "release-controller",
  "database": "ok"
}
```

SQLite 無法連線：

```http
HTTP/1.1 503 Service Unavailable
```

Response：

```json
{
  "status": "error",
  "service": "release-controller",
  "database": "unavailable"
}
```

`/health` 不要求 authentication。

---

## 10. Create Release

### POST /api/v1/releases

Request：

```json
{
  "repository": "smt-assistant-backend",
  "branch": "pre-production",
  "commit_sha": "7c1fe1ac1d4ef9b5c46279f39db965a8c73389ae",
  "environment": "pre-production",
  "message": "Drone build passed"
}
```

Server 自動建立：

```text
id
status = PENDING
created_at
updated_at
```

Response：

```http
201 Created
```

---

## 11. List Releases

### GET /api/v1/releases

支援 query parameters：

```text
repository
branch
environment
status
limit
offset
```

Example：

```http
GET /api/v1/releases?environment=pre-production&status=PENDING&limit=20
```

排序：

```text
created_at DESC
```

預設：

```text
limit = 50
offset = 0
```

最大：

```text
limit = 100
```

Response：

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

---

## 12. Get Release

### GET /api/v1/releases/{release_id}

存在：

```http
200 OK
```

不存在：

```http
404 Not Found
```

---

## 13. Approve Release

### POST /api/v1/releases/{release_id}/approve

Request：

```json
{
  "approved_by": "jhan"
}
```

只允許：

```text
PENDING -> APPROVED
```

成功時設定：

```text
status = APPROVED
approved_by
approved_at
updated_at
```

若目前不是 `PENDING`：

```http
409 Conflict
```

---

## 14. Reject Release

### POST /api/v1/releases/{release_id}/reject

Request：

```json
{
  "rejected_by": "jhan",
  "message": "Production deployment postponed"
}
```

只允許：

```text
PENDING -> REJECTED
```

成功時設定：

```text
status = REJECTED
rejected_by
rejected_at
message
updated_at
```

非 `PENDING`：

```http
409 Conflict
```

---

## 15. Error Response

統一使用 FastAPI 風格：

```json
{
  "detail": "Release not found"
}
```

至少正確區分：

```text
400 Bad Request
404 Not Found
409 Conflict
422 Validation Error
503 Service Unavailable
```

Domain conflict 不得全部回傳 `500`。

---

## 16. SQLAlchemy Requirements

使用 SQLAlchemy 2.x style。

不要使用：

```python
session.query(...)
```

優先使用：

```python
select(...)
```

Database session 必須：

- 每個 request 獨立取得 session
- request 結束後關閉
- exception 時 rollback
- service layer 控制 transaction boundary

Route 不直接堆大量 SQL 操作。

---

## 17. Alembic

專案必須包含 Alembic。

第一次 migration 建立：

```text
releases
```

Container 啟動時先：

```bash
alembic upgrade head
```

成功後才啟動 Uvicorn。

第一次部署時 `/data/release.db` 可以不存在，migration 應可自動建立 schema。

---

## 18. Logging

所有 log 輸出：

```text
stdout / stderr
```

不要寫 application log file。

Container log 由：

```bash
podman logs release-controller
```

查看。

至少記錄：

```text
Application startup
Create release
Approve release
Reject release
Unexpected exception
```

不得在 log 顯示：

```text
secret
password
token
完整 Authorization header
```

---

## 19. Containerfile

Repository root 必須有：

```text
Containerfile
```

Base image：

```Dockerfile
FROM python:3.10-slim-bookworm
```

工作目錄：

```text
/app
```

安裝：

```text
requirements.txt
```

Container port：

```text
8000
```

啟動：

```text
alembic upgrade head
```

完成後：

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Container 不內建 SQLite database file，database 由 `/data` volume 提供。

---

## 20. requirements.txt

至少包含：

```text
fastapi
uvicorn[standard]
sqlalchemy
alembic
pydantic
pydantic-settings
pytest
httpx
```

Agent 應選擇彼此相容版本並固定版本，確保 image build 可重現。

---

## 21. .gitignore

至少：

```gitignore
.env
.venv/
venv/

__pycache__/
*.py[cod]

.pytest_cache/
.coverage
htmlcov/

*.db
*.sqlite
*.sqlite3

.idea/
.vscode/

.DS_Store
```

正式 SQLite 資料不得 commit。

---

## 22. Tests

必須有 pytest。

### Health

```text
GET /health
-> 200
-> status == ok
```

### Create Release

```text
POST /api/v1/releases
-> 201
-> status == PENDING
```

### Duplicate Release

相同：

```text
repository
commit_sha
environment
```

第二次建立：

```text
409
```

### Get Release

```text
存在 -> 200
不存在 -> 404
```

### List Releases

驗證：

```text
status filter
environment filter
limit
offset
created_at DESC
```

### Approve

```text
PENDING -> APPROVED
```

驗證：

```text
approved_by
approved_at
```

已 APPROVED 再 approve：

```text
409
```

### Reject

```text
PENDING -> REJECTED
```

非 PENDING reject：

```text
409
```

---

## 23. Test Database

pytest 不得使用：

```text
/data/release.db
```

測試使用獨立 SQLite database，例如：

```text
tmp_path / test.db
```

每個測試必須可獨立執行，不依賴測試順序。

---

## 24. README

README 至少包含：

### Local development

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

export DATABASE_URL=sqlite:///./release.db

alembic upgrade head

uvicorn app.main:app --reload
```

### Test

```bash
pytest -q
```

### Container build

```bash
podman build \
  -t localhost/release-controller:latest \
  -f Containerfile \
  .
```

### Local container run

```bash
mkdir -p ./data

podman run --rm \
  --name release-controller \
  -p 127.0.0.1:3100:8000 \
  -v "$(pwd)/data:/data:Z" \
  -e DATABASE_URL="sqlite:////data/release.db" \
  localhost/release-controller:latest
```

### Health check

```bash
curl http://127.0.0.1:3100/health
```

預期：

```json
{
  "status": "ok",
  "service": "release-controller",
  "database": "ok"
}
```

---

## 25. Production Deployment

Gitea VM 已存在：

```text
scripts/deploy-release-controller.sh
```

repository clone 後：

```bash
bash ./scripts/deploy-release-controller.sh /path/to/release-controller
```

部署腳本會：

```text
檢查 Podman
↓
檢查 release-net
↓
Build image
↓
保存 rollback image
↓
移除舊 container
↓
啟動 release-controller
↓
掛載 /data
↓
執行 /health
↓
失敗則嘗試 rollback
```

Application 必須遵守：

```text
Container port = 8000
Health endpoint = /health
Database path = /data/release.db
Containerfile 位於 repo root
```

---

## 26. API Documentation

保留 FastAPI 預設：

```text
/docs
/openapi.json
```

---

## 27. Security Scope

目前 host publish：

```text
127.0.0.1:3100
```

因此第一版不實作登入系統。

但程式結構不得假設 API 永遠沒有 authentication。

後續會加入：

```text
API token
Gitea webhook secret
Drone service authentication
```

---

## 28. Future Phase

### Phase 2

```text
POST /api/v1/webhooks/gitea
```

Gitea push webhook 建立 release。

### Phase 3

Drone：

```text
test
build
↓
POST release-controller
↓
PENDING
```

### Phase 4

Approval Gate：

```text
PENDING
↓
APPROVED
↓
Drone / deployment executor
```

### Phase 5

加入：

```text
smtp-relay
```

通知：

```text
Release pending
Approved
Rejected
Deployment success
Deployment failed
```

---

## 29. Definition of Done

- [ ] `podman build` 成功
- [ ] Container 能啟動
- [ ] Alembic migration 成功
- [ ] `/data/release.db` 可以持久化
- [ ] `GET /health` 回傳 200
- [ ] 可以建立 release
- [ ] 可以查詢 release
- [ ] 可以列出 releases
- [ ] 可以 approve PENDING release
- [ ] 可以 reject PENDING release
- [ ] 非法狀態轉換回傳 409
- [ ] duplicate release 回傳 409
- [ ] pytest 全部通過
- [ ] Container log 輸出 stdout/stderr
- [ ] source tree 不產生 production SQLite database
- [ ] `scripts/deploy-release-controller.sh` 可以成功部署
- [ ] 以下指令成功：

```bash
curl -fsS http://127.0.0.1:3100/health
```

預期：

```json
{
  "status": "ok",
  "service": "release-controller",
  "database": "ok"
}
```

---

## 30. Agent Implementation Instruction

請依照本規格直接完成專案，不要只輸出範例。

完成後：

1. 執行全部 pytest。
2. 修正所有失敗測試。
3. 確認 Alembic migration 可由空資料庫建立 schema。
4. 確認 `podman build` 可成功建立 image；若開發環境沒有 Podman，至少確保 Containerfile 語法與檔案路徑一致。
5. 不修改本規格定義的 deployment contract。
6. 不加入 SMTP、Gitea webhook、Drone API 等尚未進入本階段的功能。
7. 保持程式碼可維護，不將全部邏輯集中在 route 或 `main.py`。
