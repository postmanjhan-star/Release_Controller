# Multi-Project v3.0 設計方案與遷移計畫

> 歷史版本規格：保留原始需求與設計，部分流程與部署方式已變更。
> 目前操作以[文件索引](../README.md)、[API 指南](../api-guide.md)及[部署設定](../deployment.md)為準。

> 狀態：**六個步驟全部完成**（`0014` 登錄表、`0015` 換 index 鍵、`advance()` N 步推進器、`0016` 排程元件子表、前端去前後端化、專案/連線管理頁、清理）。第 6 步**沒有產生 `0017`**——理由見 §9。
> 目標：把「一個寫死在 `.env` 的前後端專案」改成「使用者可以在 UI 自己新增、每個專案 1～N 個元件」的系統。
> 前置決策（2026-09-02 確認）：單庫兩種建模都支援 · 元件完全自訂且可選子集 · 連線設定存 DB、`.env` 只當種子。

---

## 0. 一句話總結

`Component` 這個只有兩個值的 enum，現在同時扮演四個角色：**repo 的身分**、**重複部署的鍵**、**流程階段的名字**、**bundle 的執行順序**。這次要做的事，就是把這四個角色拆開，讓前三個由資料庫的 `project_components` 資料列決定，第四個由一個 `position` 欄位決定。

---

## 1. 現況盤點：寫死「前後端」的每一個位置

| # | 檔案 | 行為 | 這次要怎麼處理 |
|---|------|------|----------------|
| 1 | `app/core/config.py:17-35` | `drone_/gitea_frontend_/backend_repo_owner/name` 八個 env 欄位 | 移除，改由 `project_components` 提供 |
| 2 | `app/core/config.py:23` | `drone_default_target` | 移到 `projects.default_target` |
| 3 | `app/db/models/orchestration.py:44-47` | `Component` enum 只有 FRONTEND / BACKEND | 改為自由字串（元件 key），enum 刪除 |
| 4 | `app/db/models/orchestration.py:240-248` | `uq_deployments_active_promotion` 鍵 = `(component, source_build_number, target)` | **改鍵**，見 §4.1 —— 全案最高風險 |
| 5 | `app/db/models/orchestration.py:103-118` | `WorkflowStage` 有 VALIDATE_FRONTEND / PROMOTE_BACKEND … 六個寫死階段 | 改為通用階段 + `component` 快照 |
| 6 | `app/services/drone_build_service.py:33-44` | `get_drone_repo()` 由 enum 拼 settings 屬性名 | 改吃 `ProjectComponent` |
| 7 | `app/services/publish_service.py:71-79` | `get_gitea_repo()` 同上 | 同上 |
| 8 | `app/services/deployment_service.py:77-90` | `promote_stage()` / `wait_stage()` 三元判斷 | 改為通用階段 |
| 9 | `app/services/release_orchestrator.py:109-116,132-145` | `create_bundle` 寫死兩個 build number、兩次驗證、backend 先 frontend 後 | 改為依 `position` 迭代 N 個元件 |
| 10 | `app/services/release_orchestrator.py:246-265` | `retry_bundle` 用 `by_component[BACKEND]` / `[FRONTEND]` | 改為「找第一個 WAITING 的」 |
| 11 | `app/services/release_orchestrator.py:275-298` | `refresh_bundle` 兩段式硬編狀態機 | 改為 §5 的 N 步推進器 |
| 12 | `app/services/release_orchestrator.py:371-397` | `_fail_bundle_after_backend` / `_finish_partial_failure` 兩個函式 | 合併為一個 `_fail_bundle()`，行為等價 |
| 13 | `app/services/publish_orchestrator.py:109` | `for component in (BACKEND, FRONTEND)` | 改為依 `position` 迭代 |
| 14 | `app/services/publish_orchestrator.py:163-167,196-200,310-314` | publish stage 三元判斷（三處） | 改為通用階段 |
| 15 | `app/services/publish_orchestrator.py:257` | `published == len(bundle.deployments)` | 分母排除 `publish_enabled=false` 的元件 |
| 16 | `app/services/schedule_service.py:145-171` | 排程用 `mode` 反推 component 與 build number | 改讀排程子表 |
| 17 | `app/db/models/scheduling.py:45-46` | `frontend_build_number` / `backend_build_number` 兩欄 | 改為 `schedule_component_builds` 子表 |
| 18 | `app/api/routes/drone.py:90,114` | 路徑參數 `{component}` 綁 enum | 改為 `/projects/{pid}/components/{key}/…` |
| 19 | `app/api/routes/drone.py:85-87` | `GET /drone/config` 回 `default_target` | 由專案 API 取代 |
| 20 | `app/api/routes/gitea.py:49-61` | `GET /gitea/config` 回前後端 repo | 由元件 API 取代 |
| 21 | `app/schemas/orchestration.py:17-21` | `BundlePromoteRequest` 兩個 build number 欄位 | 改為 `components: [{key, build_number}]` |
| 22 | `app/integrations/factory.py` | client 由 `Settings` 建構（唯一建構點） | 改由 `Connection` 建構，**仍是唯一建構點** |
| 23 | `app/workflows/bpmn/*.bpmn` | 四份靜態 XML 對應寫死模式 | 改為依專案元件動態產生 |
| 24 | `frontend/src/domain.ts:1` | `ComponentName = "frontend" \| "backend"` | 改為 `string` |
| 25 | `frontend/src/controller.ts`（82 KB） | 全域假設兩個元件 | 改為迭代 `project.components` — 前端工作量最大的一塊 |

**不受影響**（確認過）：`notification_service.py`（完全不碰 component）、`recovery_service.py`（三個 sweep 都是狀態導向、與元件無關）、`app/integrations/retry.py`、`auth_service.py`。

---

## 2. 資料模型

### 2.1 `upstream_connections`（新）

Drone 與 Gitea 共用一張表，用 `kind` 區分——UI 上就是一份「連線」清單，token 加解密邏輯只寫一次。

```
id                    str(36)  PK
kind                  str(10)  'drone' | 'gitea'          NOT NULL
name                  str(100) UNIQUE                     NOT NULL   -- 'default-drone'
base_url              str(500)                            NOT NULL
token_encrypted       text                                NOT NULL   -- Fernet
token_hint            str(8)                              NOT NULL   -- 末四碼，UI 顯示用
is_default            bool                                NOT NULL default false
verify_status         str(20)  NULL                                  -- ok / unauthorized / unreachable
verified_at           datetime NULL
created_at / updated_at

UNIQUE INDEX uq_connections_default_per_kind (kind) WHERE is_default = 1
```

- **Token 一律不明文**。用 `APP_SECRET_KEY`（env）+ Fernet（需新增 `cryptography` 依賴）。
- `APP_SECRET_KEY` 未設定且 `APP_ENV=production` → **啟動直接拒絕**，寫在 `config.py` 既有的 `refuse_unauthenticated_production` 旁邊，同一種風格。
- API 回應**永不含 token**，只回 `token_hint` 與 `has_token`。
- `POST /connections/{id}/test`：Drone 打 `/api/user`、Gitea 打 `/api/v1/version`，結果寫回 `verify_status`。

### 2.2 `projects`（新）

```
id                    str(36)  PK
key                   str(50)  UNIQUE   NOT NULL    -- 'smt-assistant'
name                  str(200)          NOT NULL
description           text     NULL
default_target        str(100)          NOT NULL default 'production'
drone_connection_id   str(36)  FK NULL  -- NULL = 用 kind='drone' 的預設連線
gitea_connection_id   str(36)  FK NULL
is_archived           bool              NOT NULL default false
created_at / updated_at
```

### 2.3 `project_components`（新）

```
id                       str(36)  PK
project_id               str(36)  FK projects.id   NOT NULL
key                      str(50)                   NOT NULL   -- 'frontend' / 'gateway' / 'admin'
display_name             str(100)                  NOT NULL
position                 int                       NOT NULL   -- 部署順序，1 起算

drone_owner              str(255)                  NOT NULL
drone_repo               str(255)                  NOT NULL
promote_target_override  str(100)  NULL            -- 單庫多元件的關鍵，見 §3
drone_connection_id      str(36)   FK NULL         -- NULL = 繼承 project

publish_enabled          bool                      NOT NULL default true
gitea_owner              str(255)  NULL            -- publish_enabled 時必填
gitea_repo               str(255)  NULL
gitea_connection_id      str(36)   FK NULL
tag_prefix               str(30)   NOT NULL default ''   -- 'fe-' / 'be-'，見 §3

is_active                bool                      NOT NULL default true
created_at / updated_at

UNIQUE (project_id, key)
UNIQUE (project_id, position)
```

### 2.4 既有表的新增欄位

| 表 | 新增 | 說明 |
|----|------|------|
| `deployments` | `project_id`, `component_id`, `drone_connection_id` | `component` 字串欄**保留**為快照（元件改名/封存後歷史仍可讀，與 `workflow_events.component` 同理） |
| `publish_records` | `project_id`, `component_id` | 同上；`repo_owner`/`repo_name` 已存在，繼續當快照 |
| `release_bundles` | `project_id`, `selected_component_keys`（逗號分隔快照） | `mode` 值域改為 `SINGLE` / `BUNDLE`，舊值原地保留 |
| `deployment_schedules` | `project_id`, `selected_component_keys` | 舊的兩個 build number 欄位由子表取代 |

⚠️ **欄位長度**：`deployments.component`、`publish_records.component`、`workflow_events.component` 目前都是 `String(20)`（夠裝 `frontend`/`backend`，不夠裝自訂名稱）。0015 要一併加寬到 `String(50)`，與 `project_components.key` 對齊——否則自訂元件名一超過 20 字元就在寫入時炸掉。
`workflow_events.component` 由 `OrchestrationWorkflowService.append()` 自動從 deployment 帶入（`workflow_service.py:230`），不需要改呼叫端。

### 2.5 `schedule_component_builds`（新）

```
schedule_id    str(36) FK deployment_schedules.id  NOT NULL
component_id   str(36) FK project_components.id    NOT NULL
build_number   int                                 NOT NULL
PRIMARY KEY (schedule_id, component_id)
```

### 2.6 刪除策略：專案與元件**不可實體刪除**

一旦有 `deployment` 或 `publish_record` 參照，專案/元件只能 `is_active=false` / `is_archived=true`。
理由與 `workflow_events` 刻意不加 FK 是同一條：**歷史是證據，不能被它描述的對象連鎖刪掉**（見 `docs`/記憶中的 C5 缺陷：刪 bundle 曾把 10 筆稽核紀錄清成 0）。
`DELETE /components/{id}` 只在零參照時成功，否則回 409 並提示改用封存。

---

## 3. 單庫專案的兩種建模（回答 Q1）

系統不預設任何一種，兩者都只是元件設定的組合：

**做法 A — 單庫單元件**（前後端一起上線）
```
project "internal-tools"
  └─ component "app"  drone=102573/tools  gitea=102573/tools  position=1
```
一次 build、一次 promote、一個 Gitea tag。最單純。

**做法 B — 單庫兩元件**（同一個 repo，前後端分別 promote）
```
project "smt-mono"
  ├─ component "backend"   drone=102573/mono  promote_target_override="production-api"  tag_prefix="be-"  position=1
  └─ component "frontend"  drone=102573/mono  promote_target_override="production-web"  tag_prefix="fe-"  position=2
```

做法 B 有兩個非做不可的配套，否則會出事：

1. **`promote_target_override`**：兩個元件若解析出同一個 `(repo, target)`，就是同一個部署槽位，第二個會被 §4.1 的 unique index 擋下，使用者只會看到一個莫名其妙的 409。
   → **在建立/更新元件的 API 就驗證**：同一專案內 `(有效 drone 連線, drone_owner, drone_repo, 有效 target)` 不得重複；重複時回 422 並直接說「請為其中一個元件設定不同的 promote target」。不要等到 promote 才炸。

2. **`tag_prefix`**：兩個元件共用同一個 Gitea repo 時，`v1.2.3` 這個 tag 只能屬於其中一個。
   → `PublishService._record_for()` 的 `tag_name` 從 `version` 改為 `f"{tag_prefix}{version}"`，`_ensure_release()` 一律用 `tag_name` 去查 tag / 建 release。`publish_records` 的 `UNIQUE (deployment_id, version)` 不受影響。

---

## 4. 併發不變式：哪些不能動，哪一條要動

`promotion_concurrency_design` 記錄的六條不變式，**五條原封不動**：

- ✅ 1 Deployment 一律建為 WAITING — 不動
- ✅ 2 只有 `claim_for_promotion()` 能把 WAITING 換成 PROMOTING — 不動
- ✅ 3 上游 HTTP 呼叫不得在交易內 — 不動。§5 的一般化只改「選誰 promote」，不改「怎麼 promote」
- ⚠️ 4 duplicate 的保證來自 partial unique index — **鍵要換，見下**
- ✅ 5 連不上 Drone 不是部署失敗 — 不動
- ✅ 6 promote 逾時 ≠ 沒送出去 — 不動

### 4.1 `uq_deployments_active_promotion` 換鍵（全案最高風險）

現在：`(component, source_build_number, target)`
改為：`(drone_connection_id, drone_owner, drone_repository, source_build_number, target)`

**為什麼不是 `(component_id, …)`**：因為要保護的事實是真實世界的——「同一個 build 不能被 promote 到同一個 target 兩次」——這跟你在 UI 上把它歸到哪個專案、哪個元件無關。用 repo 座標當鍵，連「兩個不同專案不小心指到同一個 repo」都擋得住；用 `component_id` 當鍵就擋不住。

`ACTIVE_PROMOTION_STATUSES` 述詞**不變**（仍含 WAITING）。

**上線順序（重要）**：SQLite 沒有 `ALTER INDEX`，但新舊 index 可以並存，而**舊鍵比新鍵嚴格**，所以安全順序是：
1. 先 `CREATE UNIQUE INDEX` 新的
2. 確認建立成功
3. 再 `DROP` 舊的

反過來做會出現一段完全沒有保護的空窗。

**上線前對 `/data/release.db` 跑新鍵的重複檢查**（沿用既有做法，換成新欄位）：
```sql
SELECT drone_owner, drone_repository, source_build_number, target, COUNT(*)
FROM deployments
WHERE status IN ('WAITING','PROMOTING','DEPLOYING','SUCCESS')
GROUP BY 1,2,3,4 HAVING COUNT(*) > 1;
```
（既有資料的 frontend / backend 是兩個不同 repo，理論上不會新增衝突；仍要實跑確認。）

---

## 5. Bundle 狀態機一般化

把 `refresh_bundle` 從兩段式硬編改成一個 N 步推進器。**現有兩元件的行為完全等價**。

```python
def advance(bundle):
    steps = sorted(bundle.deployments, key=lambda d: d.component_position)

    for step in steps:
        if step.status in {PROMOTING, DEPLOYING}:
            self.deployments.refresh(step, bundle)

        if step.status == FAILED:
            return self._fail_bundle(bundle, step, steps)   # 見下

        if step.status == WAITING:
            self.deployments.promote(step, bundle)          # claim → commit → HTTP，路徑不變
            if step.status == FAILED:
                return self._fail_bundle(bundle, step, steps)
            return                                          # 這一輪就到這，等下次 refresh

        if step.status != SUCCESS:
            return                                          # 還在跑，等下次

    self._finish_success(bundle)
```

`_fail_bundle()` 取代現有的 `_fail_bundle_after_backend()` 與 `_finish_partial_failure()`：

```python
def _fail_bundle(bundle, failed, steps):
    for later in steps:
        if later.status == WAITING:
            self.deployments.cancel(later, f"{failed.component} deployment failed", bundle)
    any_succeeded = any(s.status == SUCCESS for s in steps)
    bundle.status = PARTIAL_FAILURE if any_succeeded else FAILED
    ...
```

**等價性檢查**（兩元件、backend position=1、frontend position=2）：
- backend 失敗 → 沒有任何元件成功 → `FAILED` + 取消 frontend ≡ 現行 `_fail_bundle_after_backend` ✔
- frontend 失敗 → backend 已 SUCCESS → `PARTIAL_FAILURE` ≡ 現行 `_finish_partial_failure` ✔
- 兩者皆成功 → `_finish_success` ✔

`retry_bundle` 同樣簡化為：重設非 SUCCESS 的 deployment → 呼叫 `advance()`。現行那段 `if backend.status == WAITING … elif frontend.status == WAITING …` 直接刪掉。

**平行部署（暫不做）**：`position` 相同視為可平行，是自然的延伸，但第一版強制 `UNIQUE (project_id, position)`＝嚴格序列。欄位設計已預留，不需要日後改結構。

---

## 6. 流程階段與 BPMN

`WorkflowStage` 新增四個通用階段，舊的六個**保留在 enum 裡**（歷史事件讀得回來），但新事件只寫通用值：

```
VALIDATE_COMPONENT · PROMOTE_COMPONENT · WAIT_COMPONENT_DEPLOYMENT · PUBLISH_COMPONENT_RELEASE
```

「是哪個元件」由 `workflow_events.component`（既有快照欄位）表達。前端顯示時：舊值走對照表，新值用 `component` 組字串。

**BPMN**：v2.x 的四份 BPMN 從來沒被執行過，只是靜態 XML 給前端畫流程圖（SpiffWorkflow 只 parse v1 的 `release_approval.bpmn`）。所以維護 N 份靜態檔沒有意義——`routes/workflows.py` 改成**依專案元件即時產生 XML**（一個元件一組 task），失敗時回退到現有靜態檔，流程圖空白不影響部署。

---

## 7. API

### 新增

```
GET    /api/v1/connections?kind=drone|gitea
POST   /api/v1/connections
PATCH  /api/v1/connections/{id}
POST   /api/v1/connections/{id}/test
DELETE /api/v1/connections/{id}

GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{pid}
PATCH  /api/v1/projects/{pid}
POST   /api/v1/projects/{pid}/archive
POST   /api/v1/projects/{pid}/validate          -- 逐元件確認 repo 可達、target 無衝突

GET    /api/v1/projects/{pid}/components
POST   /api/v1/projects/{pid}/components
PATCH  /api/v1/projects/{pid}/components/{cid}
DELETE /api/v1/projects/{pid}/components/{cid}   -- 僅零參照時
POST   /api/v1/projects/{pid}/components/reorder -- body: [component_id, …]

GET    /api/v1/projects/{pid}/components/{key}/builds
POST   /api/v1/projects/{pid}/components/{key}/builds/{n}/promote
```

### 改動

```
POST /api/v1/releases/promote
  新 body: { project_id, target?, components: [{ key, build_number }, …] }
  舊 body: { frontend_build_number, backend_build_number, target } → 仍接受，
           視為「預設專案 + frontend/backend 兩個元件」（Phase 4 前）

GET  /api/v1/releases?project_id=…      新增過濾
GET  /api/v1/deployments?project_id=…   新增過濾

GET  /api/v1/drone/{component}/builds            → shim 到預設專案（Phase 4 移除）
POST /api/v1/drone/{component}/builds/{n}/promote → 同上
GET  /api/v1/drone/config                        → 移除（default_target 改由專案提供）
GET  /api/v1/gitea/config                        → 移除（repo 改由元件提供）
```

### ⚠️ 授權：`is_admin` 該在這裡第一次真正生效

現況：`is_admin` 有欄位、有解析、有回傳，但**全 repo 沒有任何地方用它做授權**，`require_user` 只檢查 session 存在。

專案與連線的 CRUD 是「改動正式站要部署到哪個 repo」的入口。如果沿用 `require_user`，等於**任何能登入的人都能把正式站指到別的 repo**。建議 `/projects` 與 `/connections` 的所有寫入端點改用 `require_admin`。這是新增的攻擊面，不是既有問題的延伸——值得單獨評估後再決定要不要一起做。

---

## 8. `.env` 的去留（回答 Q3）

| 設定 | 去留 |
|------|------|
| `DRONE_*_REPO_OWNER/NAME`、`GITEA_*_REPO_OWNER/NAME`（8 個） | **刪除**，改由 `project_components` |
| `DRONE_DEFAULT_TARGET` | **刪除**，改由 `projects.default_target` |
| `DRONE_SERVER` / `DRONE_TOKEN` / `GITEA_SERVER` / `GITEA_TOKEN` | **降級為種子**：`connections` 表為空時建一組預設連線；之後不再讀取 |
| `APP_SECRET_KEY` | **新增（必填）**，用來加解密連線 token |
| timeout / retry / SMTP / auth / worker / DB | **留在 env**。這些是部署層設定，不是專案設定 |

`build_drone_client(settings)` / `build_gitea_client(settings)` 改為 `ClientFactory.for_connection(connection, settings)`——**仍是唯一建構點**，timeout 與 retry policy 仍統一從 `Settings` 取（連線可覆寫），不變式維持。

---

## 9. Migration 計畫（四階段，每階段可獨立上線）

### `0014_project_registry` — 純新增，零風險
建 `upstream_connections` / `projects` / `project_components` 三張表；種子：
- 從現有 env 建 `default-drone`、`default-gitea` 兩筆連線（`is_default=true`）
- 建 `default` 專案（`default_target` = 現行 `DRONE_DEFAULT_TARGET`）
- 建 `backend`(position 1) / `frontend`(position 2) 兩個元件，repo 照現有 env

舊程式碼完全不讀新表 → **可以先上，觀察一週再繼續**。可 downgrade。

### `0015_deployments_project_scope` — 單向門
1. `deployments` / `publish_records` / `release_bundles` 加欄位（先 nullable）
2. 回填：`component='frontend'` → `default` 專案的 frontend 元件，backend 同理
3. 設 NOT NULL（SQLite 需 `batch_alter_table` 重建表——**注意保留 partial index 與 `workflow_events` 的 append-only trigger**）
4. 先建新 unique index → 確認 → 再 drop 舊的（§4.1 順序）

**上線前必跑 §4.1 的重複檢查 SQL**。此階段 downgrade 前必須確認沒有跨專案資料，否則回舊鍵會誤判重複——**視為單向門**。

### `0016_schedule_component_builds` — 純新增
建排程子表 + 回填；`frontend_build_number` / `backend_build_number` 暫留。可 downgrade。

### ~~`0017_drop_legacy`~~ — **沒有做，而且刻意不做**（2026-09-03）

原本計畫「移除排程舊兩欄、`Settings` 的 repo 設定、舊 API shim、`Component` enum」。實際動手後，
這四項只有後三項該做，第一項不該做，而且整個第 6 步**不需要任何 migration**：

**`deployment_schedules.frontend_build_number` / `backend_build_number` 保留。**
`0016` 只回填了 `status IN ('PENDING','RUNNING')` 的排程——這是當時正確的選擇，因為已跑完的排程
不需要「選了什麼」才能執行。但要**顯示**歷史就需要：對那些已完成的舊排程來說，這兩欄是它們當初
選了哪些 build 的**唯一紀錄**。刪掉不是清理，是抹掉稽核軌跡。所以改成：欄位留著當唯讀歷史、
停止寫入、`_selection_for()` 的 fallback 也留著（它現在只有 `0016` 之前的列會走到）。
既然沒有 schema 變更，也就沒有 `0017`。

`Component` enum 也保留，但意義變了：它不再是「元件的集合」，沒有任何東西再透過它解析 repository。
剩下的是「這支程式還會直接叫出名字的兩個 key」——它們有專屬的 `WorkflowStage` 成員與內建 BPMN 圖，
也是 v3.0 之前的請求 body 與排程欄位使用的名字。其他地方元件一律是字串。

---

## 10. 測試

**現有 112 條中會受影響的**：`test_concurrency_guards.py`、`test_upstream_resilience.py`、`test_release_worker_v2_2.py`、`test_release_worker_v2_3_publish.py`、`test_scheduling_notifications.py`、`test_audit_trail.py`、`test_v2_2_migration.py`。
`tests/conftest.py` 需要新增一個 `default_project` fixture（建連線 + 專案 + 兩元件），多數測試只需要改 fixture 注入。

**新增測試**：
- N=1 / N=3 的 bundle 依序推進、中途失敗的 FAILED vs PARTIAL_FAILURE 判定
- 單庫兩元件：target 衝突在建元件時被 422 擋下（不是等到 promote）
- `tag_prefix` 讓同一 Gitea repo 的兩個元件不撞 tag
- `publish_enabled=false` 的元件不計入 publish 分母
- 跨專案指到同一 repo 時，重複 promote 被新 unique index 擋下
- 連線 token 加解密往返、API 回應不含明文 token

**回歸底線**：`test_concurrency_guards.py`（真執行緒 + 檔案型 SQLite）與 `test_upstream_resilience.py` 兩份**不准放寬**——它們斷言的是修好後的行為，改壞了會紅。

---

## 11. 風險清單

| # | 風險 | 對策 |
|---|------|------|
| R1 | **換 index 鍵**是唯一擋住正式站重複部署的東西，drop/create 之間有空窗 | 先建新、確認、再刪舊（§4.1）；上線前跑重複檢查 |
| R2 | 單庫兩元件忘了設 promote target → 使用者看到莫名 409 | 建元件時就驗證並回明確訊息（§3） |
| R3 | 專案/連線 CRUD 若沿用 `require_user`，任何登入者都能改正式站指向 | 改用 `require_admin`（§7）——需單獨拍板 |
| R4 | 前端 `controller.ts` 82 KB + 測試 31 KB，改動量可能大於後端 | 建議前端獨立一輪；後端保留舊 API shim 讓兩邊解耦 |
| R5 | 動態 BPMN 產生失敗 → 流程圖空白 | 回退靜態檔；不影響部署路徑 |
| R6 | SQLite `batch_alter_table` 重建表時可能掉 partial index / append-only trigger | 0015 之後加一個「檢查 index 與 trigger 存在」的測試 |
| R7 | 多專案後更容易同時跑兩個 release，系統目前不擋 | 選配：`uq_active_bundle_per_project_target` partial unique。**會改變現有行為，建議另案** |

---

## 12. 建議施工順序

1. ~~`0014` + 連線/專案/元件 CRUD API + 加密~~ ✅ **已完成 2026-09-02**（**不接到部署路徑**，可先上線讓你在 UI 把現有專案建起來對照）
   - 新檔：`app/core/crypto.py`、`app/db/models/registry.py`、`app/schemas/registry.py`、`app/services/{connection,project}_service.py`、`app/api/routes/{connections,projects}.py`、`alembic/versions/20260902_0014_project_registry.py`、`tests/test_project_registry.py`
   - 新設定：`APP_SECRET_KEY`（production 未設拒絕啟動）、`PROJECT_ADMIN_WRITES`（預設 false，見 R3）
   - 新依賴：`cryptography==50.0.1`
   - 38 條新測試，全套 191 條綠燈，覆蓋率 88.5%
   - `_assert_deletable()`（`project_service.py`）目前是空的：0015 讓 deployments 有 `component_id` 之後要在這裡補上「有部署歷史的元件不可刪除」
2. ~~`0015` + `DroneBuildService` / `PublishService` 改吃元件 + 換 index 鍵~~ ✅ **已完成 2026-09-02**
   - 新檔：`app/services/component_registry.py`（元件解析的唯一入口）、`app/services/upstream_clients.py`（每個元件對應哪個連線的 client）、`alembic/versions/20260902_0015_deployments_project_scope.py`、`tests/test_project_scope.py`
   - `deployments` 新增 `project_id` / `component_id` / `drone_connection_id`（皆 NOT NULL）；`release_bundles`、`publish_records` 的同名欄位為 nullable
   - **與本文件三處刻意不同**（理由寫在 migration docstring 裡）：
     1. index 換鍵用**整表重建**而非「先建新再刪舊」——SQLite 加 NOT NULL 欄位本來就得重建，且整個 revision 在同一交易內，不存在「已 commit 但沒有保護」的時刻
     2. `release_bundles` / `publish_records` 的 `project_id` 維持 nullable，避免為了非約束用途再重建兩張被別人用 FK 指著的表
     3. `.env` 的 `DRONE_SERVER`/`TOKEN` **從這一步起就只是種子**——部署路徑改用 `upstream_connections` 的值。§08 原本把這個切換放在更後面
   - 全套 209 條綠燈，覆蓋率 88.5%；`test_concurrency_guards` 與 `test_upstream_resilience` 未放寬任何一條
   - 第 1 步留下的 `_assert_deletable()` 鉤子已補實：有部署歷史的元件回 409，只能停用
3. ~~`advance()` 狀態機一般化 + publish 一般化 + 通用階段~~ ✅ **已完成 2026-09-03（後端功能完成點）**
   - `refresh_bundle` / `retry_bundle` / `create_bundle` 全部改走 `_advance()`；`_fail_bundle_after_backend` 與 `_finish_partial_failure` 合併成 `_fail_bundle()`
   - `POST /releases/promote` 改收 `{project_id?, target?, components:[{key, build_number}]}`；舊的兩個 build number 仍接受
   - 部署順序由元件 `position` 決定，**不是呼叫端列的順序**；`ordered_by_component()` 是唯一的排序依據
   - `_target_for()`：元件的 `promote_target_override` **勝過** release 的 target——否則單庫兩元件會被推進同一個 promotion slot（這條是實作時發現的漏洞，原方案沒寫到）
   - publish 依同樣順序迭代；`_aggregate_bundle` 的分母只算 `publish_enabled=true` 的元件；整個 release 都不發布時回 409 而不是假裝成功
   - 新檔 `tests/test_n_component_release.py`（18 條：N=1、N=3、子集、中途失敗、retry、不發布的元件、通用階段、單庫雙 target）
   - **等價性證據**：v2.2 那套兩元件測試一條都沒改就繼續全綠；全套 227 條，覆蓋率 89.0%
4. ~~`0016` + 排程一般化~~ ✅ **已完成 2026-09-03**
   - `schedule_component_builds` 子表（PK `schedule_id, component_id`），`deployment_schedules` 加 `project_id` / `selected_component_keys`
   - `ReleaseMode` 加 `SINGLE`：一個元件 → SINGLE（走 `promote_standalone`），多個 → BUNDLE。`FRONTEND_ONLY`/`BACKEND_ONLY` 留在 enum 供舊資料使用，新資料不再寫
   - `ScheduleCreate` 收 `components`，`mode` 由數量推導（送了不一致的 mode 會被 422 擋）
   - **舊排程照跑**：`_selection_for()` 在沒有子表資料時回頭讀兩個舊欄位；migration 只回填 PENDING/RUNNING 的排程（已跑完的結果已記在 row 上，補一份選擇等於發明歷史）
   - 相容 shim：選到 frontend/backend 時仍寫舊的兩個欄位，讓還沒改的前端看得到 build number；`0017` 一併移除
   - `_assert_deletable()` 再加一條：被排程指到的元件也不能刪
   - **`0015` 一併修正**：`selected_component_keys` 原本用 `group_concat`，SQLite 不保證裡面的順序；改成逐筆依序寫入
   - 全套 243 條綠燈，覆蓋率 89.3%
5. ~~前端發布畫面去前後端化 + 專案選擇器~~ ✅ **已完成 2026-09-03**
   - 新端點 `GET/POST /projects/{project}/components/{component}/builds[/{n}/promote]`；`/drone/{frontend|backend}/builds…` 兩條舊路徑保留不動
   - `App.tsx` 不再有 `ComponentCard`：殼層只留 `#project-selector` 與空的 `#component-grid`，卡片改由 `controller.ts` 的 `renderComponentCards()` 依專案的元件清單展開（**只有一份樣板**）
   - `state.builds/branches/selected/selectedBranch/included` 全部改成以元件 key 為索引、初始為空的表；換專案就整批換掉
   - `loadRuntimeConfig()` / `loadGiteaConfig()` 刪除——target 與 Gitea repo 現在來自被選中的專案；選擇記在 `localStorage` 的 `release-controller.project`
   - **部署順序由專案的 `position` 決定**：兩份測試 fixture 刻意用相反的順序（單元測試 backend 先、e2e frontend 先），順序若被寫死回程式碼會有一邊爆掉
   - 事件改委派給 `#component-grid`（換專案會整個重畫，綁在卡片上會斷）
   - `ComponentName` 從 `'frontend' | 'backend'` 放寬成 `string`；`ui-contract.cjs` 那條不變式改成「**只有它**開放，`ReleaseMode`/`PublishStatus`/`ConnectionKind`/`HistoryView` 仍必須是封閉聯集」
   - `ui-smoke.cjs` 現在會跟著 entry chunk 內的動態 import 去讀 controller chunk——否則半個 app 不在煙霧測試的範圍內
   - **`conftest.py` 修正**：測試環境變數改在 import app 之前就寫進 `os.environ`。第 1 步加的 `APP_SECRET_KEY` 驗證會讓 `app.db.session` 在 import 期就炸，乾淨的 CI 容器裡跑 `pytest` 會直接失敗（本機有 export 才看不出來）
   - 前端 39 條單元測試 + 11 條結構不變式 + smoke + 8 條 e2e 全綠；後端 247 條、覆蓋率 89.3%
5b. ~~專案 / 連線管理頁~~ ✅ **已完成 2026-09-03**
   - 兩個新的頂端頁籤 `Projects` 與 `Connections`，沿用既有的「左清單 + 右詳情」骨架，不另外開一個設定頁
   - 專案詳情：改名 / 改預設 target / 改連線、封存與解除封存、**Check upstreams**（打 `/validate`，逐元件回報是否連得上）
   - 元件表就是部署順序：↑↓ 送 `POST /components/reorder`（送完整順序，不是相對位移），Edit / Remove 各一顆；有部署歷史的元件刪不掉，後端回的 409 訊息直接顯示在 toast
   - **key 建立後不可改**（專案與元件都是）：它寫在每一筆部署紀錄與階段名稱裡。編輯時整個 key 欄位隱藏，不是設成 disabled
   - Token 只有單向：API 回 `token_hint`，編輯連線時 token 欄位一律留空、`type="password"`，留空就是沿用舊的。多加一條結構不變式擋「順手把舊值填回輸入框」那種改法
   - 連線被專案引用時後端拒絕刪除（`ConnectionInUseError`），UI 不另外做確認框，直接讓後端說話
   - 改完元件後發布畫面的卡片會跟著更新：`componentSignature()` 比對前後，沒變就不重畫（避免每次輪詢把 build 清單洗掉），變了才重畫並重讀 builds
   - **`loadProjects()` 加上 in-flight 合併**：直接開 `?view=projects` 時 launcher 與設定頁會同時要專案清單，不合併就會選兩次專案、重抓兩次 builds
   - 前端 46 條單元測試（新增 6 條）+ 12 條結構不變式 + 9 條 e2e（新增 1 條）
6. ~~清理~~ ✅ **已完成 2026-09-03**（**沒有 migration**，理由見 §9）
   - **順手修掉一個 v3.0 自己造成的缺陷**：`GET /deployments?component=` 與 `/publishes?component=` 的型別是
     那個兩值 enum，所以第三個元件根本**沒辦法被篩選**——而且失敗方式是 422，看起來像請求寫錯，
     不像「這支程式表達不出你的元件」。改成字串；查不到的 key 回空清單而不是錯誤
   - 刪掉會「猜專案」的 v2.2 shim：`GET/POST /drone/{component}/builds[...]` 與
     `ComponentRegistry.for_legacy()`。它們透過 `default_project()` 解析，多專案之後就是安靜地對某一個
     專案動手——會猜的 API 比沒有 API 更糟
   - 刪掉 `GET /drone/config` 與 `GET /gitea/config`：它們回的 default target 與 Gitea repo 現在是每個專案自己的
   - `Settings` 移除 9 個 repo 欄位。**前提是 migration `0014` 先改成直接讀 `os.environ`**——
     migration 是凍結的歷史，不該依賴會繼續演化的 app 設定模型
   - `.env.example`、`README.md`、部署腳本一起更新。部署腳本的必填清單移除那些 repo 變數，
     **改成必填 `APP_SECRET_KEY`**：正式環境沒有它會拒絕啟動，在部署腳本擋下來遠比啟動一個馬上結束的
     container 好
   - 排程不再寫舊兩欄（欄位仍是唯讀歷史）
   - 後端 247 條綠燈、覆蓋率 89.4%；前端 46 條 + 12 條不變式 + 9 條 e2e 全綠

第 1、2 步之間是天然的暫停點：那時 UI 已能新增專案，但部署仍走舊路徑，隨時可以停下來重新評估。

---

## 13. 待你決定

- **R3 的授權**：專案/連線寫入要不要一併啟用 `is_admin`？（現在它形同虛設。管理頁已經做好，任何登入者都能改專案設定——`PROJECT_ADMIN_WRITES=true` 之前請先確認 `/api/v1/auth/me` 的 `is_admin` 是 true，否則會把自己鎖在外面）
- **R7 的同專案並行 release**：要擋還是先不擋？
- **施工順序**：是否照 §12 的第 1 步先上，讓你先在 UI 把現有兩個專案建起來對照，再往下走？
