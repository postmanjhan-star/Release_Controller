# 後端架構審查：DDD / Onion Architecture 全面對照

審查對象：`app/`（FastAPI 0.116 + SQLAlchemy 2.0 + Alembic + SQLite，9,683 行 / 73 個 .py）
審查基準：dddpy 風格的 Onion Architecture 四層（Domain / Infrastructure / UseCase / Presentation）
日期：2026-09-04
性質：**只分析，不動 code**

---

## 實作進度

Phase 0 與 Phase 1 已完成，在分支 `refactor/ddd-phase-0-1`，各一個 commit，
每個 commit 都跑過 `uv run quality`（ruff check / ruff format / 247 個測試 / branch coverage）。

| 階段 | 狀態 | commit |
|---|---|---|
| Phase 0 領域 enum 與 `utc_now` 抽到 `app/domain/` | ✅ | `refactor: move the domain enums out of the persistence layer` |
| Phase 1 DI 集中到 `app/infrastructure/di/injection.py` | ✅ | `refactor: wire the dependencies in one place` |
| Phase 2 v1 `Release` 四層垂直切片 | ✅ | `refactor: give the v1 release aggregate its four layers` |
| Phase 3a `Connection` aggregate | ✅ | `refactor: give the connection aggregate its four layers` |
| Phase 3b `Project` / `Component` | ✅ | `refactor: give the project aggregate its four layers` |
| Phase 3c-1 orchestration 狀態機進 domain | ✅ | `refactor: extract the orchestration state machines into the domain` |
| Phase 3c-2 接上 repository 與 use case | ⏸ 進行中 | |
| Phase 4 error_messages / transaction 邊界 | ⏸ 未做 | |

### 量到的依賴變化

| 依賴邊 | 施工前 | Phase 0 後 | Phase 1 後 | Phase 2 後 |
|---|---:|---:|---:|---:|
| `schema → db` | 5 | **0** | 0 | 0 |
| `presentation → db` | 18 | 12 | **6** | **5** |
| `service → db` | 37 | **26** | 26 | **24** → **23**（3a）→ **20**（3b）|
| `service → schema` | 11 | 11 | 11 | **10** → **9**（3a）→ **8**（3b）|
| `integration → db` | 1 | 1 | 1 | 1 → **0**（3a）|
| `presentation → presentation` | 15 | 15 | **2** | 2 |
| route 之間互相 import | 6 | 6 | **0** | 0 |
| `presentation → service` | 41 | 41 | **34** | **32** |
| `presentation → core` | 12 | 12 | **7** | 7 |
| `presentation → integration` | 11 | 11 | **9** | 9 |
| `usecase → domain` | — | — | — | **31** |
| `usecase → 其他任何層` | — | — | — | **0** |
| `domain → 其他任何層` | — | **0** | 0 | **0** |

`presentation → db` 兩階段都有下降，原因不同：Phase 0 拿掉的是 6 條 enum import，
Phase 1 拿掉的是 6 個 route 模組原本為了自建工廠而需要的 `get_db`。

新增的 `app/domain/` 有 22 條內部邊、**對外零依賴**；`app/usecase/` 的 31 條
依賴**全部指向 domain**，沒有一條碰到 db、service、integration 或 schema。
這兩件事現在有測試守著（`tests/test_release_domain.py` 最後兩個），
不是靠 review 時記得檢查。

### 順手修掉的三件事

| 發現 | 處置 |
|---|---|
| `drone.py:get_orchestrator` 與 `releases.py:get_release_orchestrator` 是 byte-identical 的重複工廠 | 只留 `get_release_orchestrator` |
| `drone.py` 定義 `_registry_http_error` / `_upstream_http_error` 但自己從不使用，只有 `projects.py` 跨檔借用 | 移到 `app/api/errors.py` 並改為公開 |
| `app/api/auth.py` 的三個守衛也是相依提供者，留在 `app/api/` 會讓 infrastructure 為了組出帶 actor 的 orchestrator 而反向 import presentation | 併入 `injection.py`，`app/api/auth.py` 刪除 |

### Phase 2 做出來的樣子

```
app/domain/release/
    entities/release.py                  Release，四個轉移，零 framework
    value_objects/release_status.py      （Phase 0 已就位）
    repositories/release_repository.py   ReleaseRepository(ABC) + ReleaseFilters
    repositories/workflow_gateway.py     ReleaseWorkflowGateway(Protocol)
    exceptions/release_exceptions.py     三個業務例外
app/domain/shared/unit_of_work.py        UnitOfWork(ABC)
app/infrastructure/sqlite/
    unit_of_work.py                      SqlAlchemyUnitOfWork
    release/release_dto.py               to_entity / from_entity / mutable_values
    release/release_repository.py        帶 WHERE 的 UPDATE 還在這裡
app/usecase/release/
    _transition.py                       四個轉移共用的執行順序
    create / get / list / approve / reject / start_deployment / finish_deployment
```

`app/services/release_service.py` 已刪除。

**兩個工作拆開了，而且兩個都還在做**：

| | 在哪裡 | 擋得住什麼 |
|---|---|---|
| 「只有 PENDING 能核准」這條規則 | `Release.approve()` | 寫錯的呼叫。不碰資料庫就能測 |
| 兩個人同時按核准 | `save_transition()` 的 `WHERE status = expected` | 競賽。記憶體檢查看不到另一個請求 |

`approve()` 回傳它的前置狀態，UseCase 把它交給 repository 當 CAS 條件——
這個看起來多餘的回傳值就是不讓規則和寫入條件各走各的。**只把規則搬進 entity
然後無條件 save，看起來會很像 DDD，而且會安靜地把每次轉移變成
last-write-wins。**

**順手修好的一個 v1 缺陷**：輸掉競賽的那一方，v1 會重讀自己那份舊快照來組錯誤
訊息，講出「status is PENDING; expected PENDING」這種話。現在 CAS 失敗會先
`rollback()` 再重讀，所以它報的是資料庫真正的狀態。

### 新增的 20 個測試，每個都對壞掉的版本驗證過會失敗

| 測試 | 故意弄壞什麼 | 結果 |
|---|---|---|
| 19 個 entity 規則測試（含 2 個結構不變式） | 在 entity 裡 `import sqlalchemy` | `test_the_domain_layer_imports_no_framework` 失敗 ✅ |
| 兩個執行緒同時核准，只有一個成功 | 拿掉 `save_transition` 的 `status == expected` | 並行測試失敗 ✅ |

19 個 entity 測試跑 **0.05 秒**，完全不建資料庫——這是 Phase 2 真正買到的東西。
並行測試用真實檔案型 SQLite 加兩個執行緒，連跑 10 次都穩定。

**契約沒有動**：67 個端點的 OpenAPI 產出與 `main` 逐字相同。
267 passed（原本 247），coverage 90.01%（原本 89.50%）。

### Phase 3 的順序被實測推翻了

報告原本建議 `Publish → Schedule → Deployment → Bundle`，理由是「由外而內、由小
而大」。真的去量之後，這個順序是錯的。

用 AST 檢查每個 service **直接改寫**哪些 ORM model 的欄位（不是讀，是寫）：

| service | 改寫自己 aggregate | 改寫**別的** aggregate |
|---|---|---|
| `connection_service` | UpstreamConnection 14 | — |
| `project_service` | Project 24 / ProjectComponent 15 | — |
| `publish_service` | PublishRecord 15 | **Deployment 21** |
| `schedule_service` | DeploymentSchedule 11 | **Deployment 11** |
| `publish_orchestrator` | ReleaseBundle 24 | **Deployment 11** |
| `release_orchestrator` | ReleaseBundle 23 | （透過 deployment_service） |
| `deployment_service` | Deployment 51 | **ReleaseBundle 9** |

`publish_service` 改寫 `Deployment` 的次數（21）**比改寫 `PublishRecord`（15）還多**。
原因很實在：`publish_status`、`version`、`publish_started_at` 這些欄位就長在
`deployments` 表上，`PublishRecord` 是它的子實體。

**結論**：`Deployment` / `ReleaseBundle` / `PublishRecord` / `DeploymentSchedule`
在 DDD 的意義上是**一個 aggregate**，不是四個。硬拆成四個，就會製造出上表右欄
那些跨 aggregate 寫入——也就是我們想消滅的東西。

真正獨立的是 registry：`Connection` 與 `Project`，右欄是空的。

**修正後的順序**：

1. ✅ **Connection**（Phase 3a，已完成）——右欄空的，最小的完整切片
2. **Project / Component**——右欄也是空的，但要先有 Connection（它引用連線）
3. **Orchestration**——`Deployment` 為根，`ReleaseBundle`、`PublishRecord`、
   `DeploymentSchedule` 在同一個邊界裡，**一次做完**而不是拆四刀

### Phase 3a：Connection aggregate ✅

這是第一個規則不是狀態機的切片。兩條規則原本埋在 `ConnectionService` 裡，
兩條都要起資料庫、發 HTTP、再 stub 掉 Drone 才測得到：

- 尾端斜線不算差異——`https://drone/` 和 `https://drone` 是同一台
- **換 token 就要丟掉驗證結果**——留著等於拿舊 token 的「ok」去描述一把還沒被
  試過的新 token

兩條現在都在 entity 上，單元測試 0.02 秒跑完。

**三個不是持久化的出口，也給了 domain Protocol**：

| Port | 為什麼不能留在 domain 裡做 |
|---|---|
| `TokenVault` | 加密要金鑰，金鑰來自設定 |
| `ConnectionProbe` | 要真的打上游 |
| `ConnectionUsage` | 跨 aggregate 的唯讀查詢（還有誰指著這條連線） |

放在 `repositories/` 裡，跟 Phase 2 的 workflow gateway 同一個位置——都是
domain 宣告、infrastructure 實作的出口，即使談的不是資料庫。

**`clear_default` 仍然是一句 UPDATE**，而且理由寫在 repository 裡：逐列讀出來改
也做得到，但中間會有一瞬間同種類有兩個預設，那正是
`uq_upstream_connections_default_per_kind` 拒絕的事。跟 Phase 0 對
`ACTIVE_PROMOTION_*` 的判斷同一條原則。

**順手還掉的技術債**：`UpstreamConnectionProbe` 的 client builder 改成建構參數，
於是 `tests/test_project_registry.py` 裡最後 4 個 `monkeypatch.setattr(模組, ...)`
消失了。那種寫法在報告的「測試防護網」段落被點名為最容易碎的地方——模組一搬家，
測試會安靜地不再測到東西而不是報錯。改完之後它們覆寫的是注入的相依，而且仍然
測到真正的「例外 → 狀態」對照邏輯。

`app/integrations/factory.py` 改收 domain entity，於是 `integration → db` 這條邊
歸零。

### Phase 3b：Project aggregate ✅

第一個有真正 aggregate root 的切片。Project 擁有它的 Component，因為這裡每一條
規則講的都是「這一組」而不是「這一個」。

**最重要的規則搬家了。** 模組 docstring 本來就寫著 `_assert_no_slot_conflict` 是
這個檔案的重點，但它是三個私有方法拼起來的，要測「兩個元件撞在同一個促銷位置」
得先起 SQLite、存兩條連線、建專案、再發一個 HTTP 請求。現在是
`Project.slot_conflicts()` 的純函式行為，三行就測得到。

連線解析留在外面：use case 把 `component.id -> connection.id` 的對照表交給 entity，
解析不出來的統一用一個 sentinel 分組——所以兩個都在等同一個不存在的預設連線的
元件，仍然會被算成相撞。這跟 Phase 2 的「規則進 entity、要讀資料庫的事留在外面」
是同一個切法。

**位置也一樣**：aggregate 保證 1..n 連續，而「分兩趟走負數」留在 repository，
理由寫在那裡——SQLite 沒有 deferrable constraint，只要不是純 append，直接寫最終
順序就會有一瞬間兩個元件位置相同。

**寫 entity 測試時抓到一個二十分鐘前才寫下的 bug**：`add_component` 沒有讓新元件
重新繼承專案的預設值，所以在專案載入之後才加進來的元件，會沿用建構當下傳進去的
`default_target` 與連線 id，而不是專案現在的設定。修掉了，抓到它的測試留著。

`UpstreamComponentChecker` 的 client builder 也改成建構參數，於是最後 3 個
`monkeypatch.setattr(模組, ...)` 也消失了——**`tests/` 裡現在沒有任何一處在
patch 模組屬性**。

`app/services/` 從 17 個檔案 4,821 行降到 14 個檔案 3,915 行。

**兩個過渡 shim**（都寫了註解，Phase 3c 會消失）：`component_registry` 的
`project_connection_id` 與 `project_default_target` 同時接受 ORM 列與 domain
entity——orchestration 那側還在傳 ORM 列，registry 這側已經是 entity。

### Phase 3c：orchestration——為什麼這一刀切法不同

前面四刀每一刀都是「一個 aggregate，一個 commit，各自可獨立合併」。這一刀不行。

`deployments` 這張表有**六條寫入路徑**：`deployment_service`、
`release_orchestrator`、`publish_service`、`publish_orchestrator`、
`schedule_service`、`recovery_service`。它們必須一起搬，否則同一列會有兩種寫法
——而那正是「索引與檢查同源」那類註解在防的事。

範圍也不一樣：這一叢集 3,915 行，其中核心的 `Deployment` + `ReleaseBundle`
是 1,188 行。決定是**只做核心**：`publish` / `schedule` / `recovery` /
`notification` 留在 service 層，改成透過新 aggregate 的 repository 存取。
它們本來就是 application service 而不是 aggregate root，這是一個誠實的終點，
不是半套。

**3c-1（已完成）**：兩個 entity 的規則加測試，還沒有接上去。

先加 entity 再接線，是為了讓規則在變成承重結構之前就被驗證過。搬進去的東西裡有
兩條是「容易寫錯而且今天完全測不到」的：

| 規則 | 寫錯的後果 |
|---|---|
| 促銷沒被確認時維持 `PROMOTING` | 標成 `FAILED` 會同時弄丟一個可能正在跑的正式部署，**並且釋放重複保護的位置**——下次重試會促銷第二次 |
| 重試要清掉 `started_at` | 不清的話 timeout 會拿上一次嘗試的時間來算這一次 |

還有 poll failure「只在狀況改變時發事件」（否則每 5 秒一行會把 timeline 淹掉）、
frontend/backend 保留歷史 stage 名稱（既有事件與 BPMN 圖才對得上），以及
bundle 的兩條規則：下一步要動哪個元件，以及 `PARTIAL_FAILURE` 就是
「失敗，但已經有東西上線了」。

27 個測試、0.07 秒、零資料庫零 Drone。其中兩個先對故意寫壞的 entity 驗證過：
把 unconfirmed 改成標 FAILED、把 `started_at` 的清除拿掉，各自只讓一個測試變紅。

**3c-2（未完成）**：`DeploymentRepository`（含 claim 的 CAS 與建立時的重複保護）、
`ReleaseBundleRepository`、`WorkflowEventRecorder` port、use cases，然後把上面
六條寫入路徑一起改過去。

### 需要注意的行為面

`app/db/models/__init__.py` 不再轉出領域 enum，只保留 16 個 ORM 類別——
`alembic/env.py` 靠 `from app.db import models` 註冊 `Base.metadata`，所以那份清單
仍然必須完整，這點沒有改變。

`tests/` 有 8 個檔案原本從 route 模組 import DI 工廠來做 `dependency_overrides`，
已改指 `injection.py`。`dependency_overrides` 的 key 是函式物件本身，指錯地方會安靜
失效而不是報錯，所以這 8 處是這次改動裡最需要盯著測試的地方（都有跑到）。

### API 契約沒有動：逐項比對過

除了測試之外，另外把 `main` 與 `refactor/ddd-phase-0-1` 兩邊的 OpenAPI 產出
（67 個端點的 method、path、query 參數名、response 狀態碼、有無 request body）
dump 出來逐字比對：

```
main:  67 endpoints
after: 67 endpoints
diff:  完全相同
```

由於 FastAPI 的 OpenAPI 是從實際註冊的路由與 `Depends` 簽章反推出來的，
這代表相依重接之後，路由表與每個端點的介面都沒有變。

### 未驗證的部分

- 前端 gate 與 Playwright E2E 沒有跑：這次只動後端 Python，`frontend/` 一行未改。
- 沒有實際啟動服務打真的 Drone／Gitea。執行期相依解析靠 247 個測試涵蓋
  （其中 16 個檔案走 `TestClient`，會真的走完整的 DI 解析）。

---

## 一句話結論

這份程式碼不是「還沒學會 DDD 的分層」，而是「刻意把不變量放進資料庫的分層」——
所以正確的目標不是照抄 dddpy，而是**把領域概念從 `app/db/models/` 裡拿出來，
把狀態機從 SQL 的 `WHERE` 子句搬進 Entity，但把並行保證留在資料庫**。

## 實測依賴矩陣

用 AST 掃全部 73 個檔案的 `import`，依 Onion 規則判定（內層不得依賴外層）：

| 依賴邊 | 次數 | Onion 判定 |
|---|---:|---|
| presentation → service | 41 | ✅ 允許 |
| service → db | 37 | ❌ UseCase 直接依賴持久化實作 |
| service → service | 31 | ⚠️ 網狀，非單向 |
| service → integration | 20 | ❌ UseCase 直接依賴外部 client |
| presentation → db | 18 | ❌ 其中 6 條是 enum，不是 session |
| presentation → integration | 11 | ❌ 跨兩層 |
| service → schema | 11 | ❌ Pydantic（presentation 概念）反向進入業務層 |
| schema → db | 5 | ❌ DTO 依賴 ORM |
| service → core | 17 | ⚠️ `Settings` 貫穿所有層 |

Onion 只允許「外 → 內」。目前 **102 條邊被判為 ❌**（其中 `presentation → db` 的 18 條裡，
12 條是 `get_db`、6 條是 enum），真正會痛的集中在
`service → db`（37）與 `service → integration`（20）：這兩者合起來的意思是
**業務邏輯無法在沒有 SQLite 和沒有 Drone/Gitea 的情況下執行**。

## 結論摘要：依價值排序的 8 項落差

| # | 落差 | 證據 | 修的價值 | 修的成本 |
|---|---|---|---:|---:|
| 1 | 沒有 Domain 層，14 個領域 enum 住在 `app/db/models/` | `grep '(str, enum.Enum)' app/db/models/` | 高 | **低** |
| 2 | Entity 貧血，狀態機寫在 SQL `WHERE` 裡 | `release_service.py:99-201` | 高 | 中 |
| 3 | 沒有 Repository interface，依賴方向未反轉 | 37 條 `service → db` | 高 | 高 |
| 4 | 沒有 UseCase 層，Service 是多責任胖類別 | `DeploymentService` 704 行 / 19 方法 | 中 | 高 |
| 5 | DI wiring 散在 route 檔，route 互相 import | `releases.py:7-8` | 中 | **低** |
| 6 | 沒有 DTO 邊界，ORM 物件直通 HTTP | 12 處 `from_attributes=True` | 中 | 中 |
| 7 | 例外沒分層，33 個 Error 定義在 service，77 處 `HTTPException` | `releases.py` 單檔 30 處 | 低 | 低 |
| 8 | Transaction 邊界散落，12 個檔案 43 處 `.commit()` | `release_orchestrator.py` 單檔 7 處 | 中 | 中 |

**成本低、價值高的是 #1 和 #5**——這兩項可以各用一個 commit 完成，且不改變任何行為。
其餘的建議先做一個垂直切片證明模式可行，再決定要不要全面推。

---

## 這個專案的特殊性：為什麼不能照抄 dddpy

dddpy 是一個 Todo app。這是一個**多專案發布協調器**，兩者在三件事上根本不同，
直接套規範會把現有的正確性弄壞：

### 1. 不變量刻意放在資料庫，不在應用層

`app/db/models/orchestration.py:287-298` 自己寫得很清楚：

```python
# The database, not the application, is what makes duplicate promotion
# impossible.  DeploymentService.assert_not_duplicate is only a fast path
# for a friendly error message; between its SELECT and the INSERT another
# request can slip through, so this index is the actual guarantee.
Index("uq_deployments_active_promotion", *ACTIVE_PROMOTION_SLOT, unique=True, ...)
```

`workflow_events` 的 append-only 也一樣，是 SQLite trigger 擋的
（`orchestration.py:483-501`），不是「大家說好不要 update」。

**這是對的。** 教科書 DDD 會說「不變量屬於 aggregate」，但 aggregate 保護不了
兩個 process 同時進來。這一項在遷移中必須原封不動。

### 2. 狀態轉移是 compare-and-swap，不是讀-改-寫

`release_service.py:103-117`：

```python
result = self.db.execute(
    update(Release)
    .where(Release.id == release_id, Release.status == ReleaseStatus.PENDING)
    .values(status=ReleaseStatus.APPROVED, approved_by=..., approved_at=now)
)
if result.rowcount == 0:
    self._raise_missing_or_conflict(release_id)
```

`WHERE status == PENDING` 同時是**業務規則**（只有待審核的能核准）和**並行控制**
（兩個人同時按核准，只有一個 rowcount 會是 1）。

dddpy 的寫法是 `release.approve()` 然後 `repository.save(release)`——那會退化成
last-write-wins，兩個核准都會成功。**遷移時這兩個角色必須拆開**：規則進 Entity，
CAS 留在 repository 的 save 條件裡。這是整份報告最關鍵的一點。

### 3. 「Domain」有一半在 BPMN 檔案裡

`app/workflows/bpmn/` 的 XML 定義流程，SpiffWorkflow 執行它，
穩定 stage ID（`PROMOTE_BACKEND`、`WAIT_FRONTEND_DEPLOYMENT`…）同時被 Python、
序列化的 workflow instance 和前端 bpmn-js viewer 依賴。

這一塊不要碰。改 element ID 需要 instance migration，而不是重構。

---

## 逐項對照

### 落差 1｜沒有 Domain 層，領域概念住在持久化模組

**規範**：Domain 層零外部依賴，不 import 任何 framework。

**現況**：`app/` 底下沒有 `domain/`。14 個領域 enum 全部定義在 `app/db/models/`：

| enum | 位置 | 是不是領域概念 |
|---|---|---|
| `Component` | `orchestration.py:44` | 是 |
| `ReleaseMode` | `orchestration.py:59` | 是 |
| `DeploymentStatus` | `orchestration.py:71` | 是（狀態機的字母表） |
| `BundleStatus` | `orchestration.py:114` | 是 |
| `PublishStatus` | `orchestration.py:123` | 是 |
| `PublishRecordStatus` | `orchestration.py:131` | 是 |
| `WorkflowStage` | `orchestration.py:137` | 是（且被 BPMN 依賴） |
| `WorkflowEventType` | `orchestration.py:162` | 是 |
| `ActorSource` | `orchestration.py:475` | 是（稽核概念） |
| `ConnectionKind` | `registry.py:35` | 是 |
| `ConnectionVerifyStatus` | `registry.py:40` | 是 |
| `ReleaseStatus` | `release.py:15` | 是（v1 狀態機） |
| `ScheduleStatus` | `scheduling.py:21` | 是 |
| `EmailDeliveryStatus` | `scheduling.py:29` | 是 |

連 `utc_now()` 這個純時間工具也住在 `app/db/models/release.py:11`，
然後被 **13 個檔案**跨層 import——包含 4 個其他 model、9 個 service。
一個和 release 完全無關的通知服務，為了取得「現在幾點」得先 import
release 的 ORM 模組。

**後果（可量化）**：`app/schemas/` 有 5 條、`app/api/routes/` 有 6 條 import 存在的唯一
理由，就是這些 enum 放錯地方：

```
app/schemas/release.py:6           from app.db.models.release import ReleaseStatus
app/schemas/registry.py:7          from app.db.models.registry import ConnectionKind, ...
app/schemas/orchestration.py:13    from app.db.models.orchestration import (...)
app/schemas/scheduling.py:14-15    from app.db.models.orchestration/scheduling import ...
app/api/routes/drone.py:6          from app.db.models.orchestration import ActorSource
app/api/routes/connections.py:8    from app.db.models.registry import ConnectionKind
app/api/routes/releases.py:10-11   from app.db.models.orchestration/release import ...
app/api/routes/deployments.py:9    from app.db.models.orchestration import ...
app/api/routes/schedules.py:9      from app.db.models.scheduling import ScheduleStatus
```

**判定**：❌ 最根本的落差，但也是**最便宜修的**。純搬移，沒有行為改變。

---

### 落差 2｜Entity 貧血，狀態機表達在 SQL 而非物件

**規範**：Entity 有身分、可變狀態、**封裝業務邏輯**；`todo.start()` 而不是
`UPDATE todos SET status='IN_PROGRESS'`。

**現況**：ORM model 幾乎純資料。16 個 ORM model 類別中，只有 2 個帶方法：

- `ProjectComponent.effective_target / drone_slug / gitea_slug`（`registry.py:124`）
- `DeploymentSchedule.notification_status / notification_sent_at`（`scheduling.py:36`）

而且全是 derived property，不是狀態轉移。真正的狀態機在
`app/services/release_service.py:99-201`，四個方法長得一模一樣：

| 方法 | 規則 | 表達方式 |
|---|---|---|
| `approve` L99 | 只有 `PENDING` 能核准 | `.where(Release.status == PENDING)` |
| `reject` L124 | 只有 `PENDING` 能駁回 | `.where(Release.status == PENDING)` |
| `start_deployment` L150 | 只有 `APPROVED` 能開始 | `.where(Release.status == APPROVED)` |
| `finish_deployment` L174 | 只有 `DEPLOYING` 能結束 | `.where(Release.status == DEPLOYING)` |

**後果**：

1. 「Release 有哪些合法轉移」這件事，讀 `Release` 類別看不到，要讀四個 service 方法的
   `WHERE` 子句才拼得出來。
2. 規則沒辦法單元測試——測「REJECTED 不能再 approve」必須起一個 SQLite。
3. `_raise_invalid_transition`（L206）為了組出錯誤訊息，還要再 `get()` 一次，
   等於一個轉移最多打三次 DB。

**但是**：如同前面說的，那個 `WHERE` 同時是並行控制。改成
`release.approve()` + `repo.save(release)` 會靜默地弄壞雙重核准的防護，
而且現有測試（`tests/test_releases.py`）不見得抓得到。

**正確的拆法**：

```python
# domain/release/entities/release.py —— 規則，可單元測試
def approve(self, by: Actor, at: datetime) -> None:
    if self._status is not ReleaseStatus.PENDING:
        raise InvalidReleaseTransitionError(self._status, expected=ReleaseStatus.PENDING)
    self._status = ReleaseStatus.APPROVED
    ...

# domain/release/repositories/release_repository.py —— 並行保證還是給 DB
@abstractmethod
def save_if_status(self, release: Release, *, expected: ReleaseStatus) -> bool: ...
```

Repository 實作照舊發 `UPDATE ... WHERE status = expected`，回傳 `rowcount == 1`。
**規則進 entity，CAS 留在 SQL，兩邊都不犧牲。**

**判定**：❌ 但修法有陷阱，必須成對處理。

---

### 落差 3｜沒有 Repository interface，依賴方向未反轉

**規範**：Domain 定義 `ABC`，Infrastructure 實作，UseCase 只認得 `ABC`。

**現況**：37 條 `service → db.models`，每個 service 都直接持有 `Session`：

```python
class ReleaseService:
    def __init__(self, db: Session) -> None:   # release_service.py:41
        self.db = db
```

**14 個 service 類別**全部是這個形狀，連型別註記都直接寫 `sqlalchemy.orm.Session`。
沒有任何一個 repository 抽象存在——實測 `app/` 全樹**零個 `ABC` 或 `abstractmethod`**。

**後果**：

- 業務邏輯的測試必須有真 SQLite。`tests/` 21 個檔案裡，**11 個直接 import
  `app.services`**（`test_audit_trail.py:34`、`test_concurrency_guards.py:36-38`、
  `test_project_scope.py:185` 等），這些測試同時綁定業務規則與持久化實作。
- 換 DB（README 提到未來要離開 SQLite 才能水平擴充）等於改 13 個 service。

**判定**：❌ 價值最高但成本也最高，建議只在垂直切片裡先做一個。

---

### 落差 4｜沒有 UseCase 層，Service 是多責任胖類別

**規範**：一個 UseCase 一個責任，只有一個公開的 `execute()`。

**現況**：最大的三個 service：

| 檔案 | 行數 | 公開方法數 | 混了哪些責任 |
|---|---:|---:|---|
| `deployment_service.py` | 704 | 12（另 6 個私有） | 重複檢查、狀態轉移、Drone API、workflow event 寫入、查詢、逾時判定 |
| `project_service.py` | 488 | 11（另 11 個私有） | CRUD、slot 衝突、連線驗證、Drone 探測、重新編號 |
| `release_orchestrator.py` | 480 | 8（另 5 個私有） | bundle 建立、元件挑選、重試、推進、失敗聚合、列表查詢 |

`DeploymentService.__init__`（L105-118）一次組進 `Session`、`DroneBuildService`、
`Settings`、`ComponentRegistry`、`OrchestrationWorkflowService` 五個相依，
其中 `ComponentRegistry` 和 `OrchestrationWorkflowService` 是它自己 new 出來的
（L117-118）——不是注入，所以測試沒辦法替換。

**service → service 共 31 條，且是網狀不是分層**：

```
publish_orchestrator  → component_registry, deployment_service, publish_service,
                        release_orchestrator, upstream_clients, workflow_service   (6)
release_orchestrator  → attachment_service, component_registry, deployment_service,
                        drone_build_service, workflow_service                      (5)
background_worker     → notification_service, recovery_service,
                        release_orchestrator, schedule_service                     (4)
schedule_service      → attachment_service, component_registry,
                        notification_service, release_orchestrator                 (4)
```

`publish_orchestrator` 依賴 `release_orchestrator`，而 `schedule_service` 也依賴
`release_orchestrator`，`background_worker` 又依賴 `schedule_service`——
要理解「排程觸發一次發布」得同時打開 4 個檔案。

**判定**：❌ 但這是 #3 的下游；沒有 repository 就拆不乾淨 UseCase。

---

### 落差 5｜DI wiring 散在 route 檔案，route 互相 import

**規範**：`app/infrastructure/di/injection.py` 一處組裝
session → repository → usecase → handler。

**現況**：109 處 `Depends()`，工廠函式散在 8 個 route 檔案裡，然後 route 互相 import
工廠——形成 6 條 presentation 內部的橫向依賴：

```
app/api/routes/releases.py:7     from app.api.routes.deployments import get_publish_orchestrator
app/api/routes/releases.py:8     from app.api.routes.drone     import get_build_service
app/api/routes/deployments.py:7  from app.api.routes.drone     import get_build_service, get_component_registry
app/api/routes/projects.py:7     from app.api.routes.drone     import (...)
app/api/routes/schedules.py:7    from app.api.routes.drone     import get_build_service
app/api/routes/publishes.py:6    from app.api.routes.gitea     import get_gitea_client
```

`drone.py` 事實上變成了 DI 容器——4 個其他 route 檔向它要工廠。它同時還是一個
有 4 個 endpoint 的 API 模組。

**後果**：要換掉 `DroneBuildService` 的建構方式，得改 `drone.py`，而它的變更會
連帶影響 releases / deployments / projects / schedules 四個路由檔的 import 圖。

**判定**：❌ 但這是純機械搬移，**不需要先做 #1~#4**，可以獨立完成。

---

### 落差 6｜沒有 DTO 邊界，ORM 物件直通 HTTP

**規範**：層與層之間走 DTO，用 `to_entity()` / `from_entity()` 轉換。

**現況**：`app/schemas/` 12 處 `model_config = ConfigDict(from_attributes=True)`，
service 直接回 ORM 實例，route 靠 `response_model` 序列化：

```python
# release_service.py:45-62 —— 回傳 SQLAlchemy 物件
def create(self, payload: ReleaseCreate) -> Release: ...

# releases.py:92-95 —— 直接當成 ReleaseResponse 丟出去
@router.post("", response_model=ReleaseResponse, ...)
def create_release(payload: ReleaseCreate, service: Service) -> ReleaseResponse:
    return service.create(payload)
```

反方向也一樣：`release_service.py:46` 是
`Release(**payload.model_dump(), status=ReleaseStatus.PENDING)`——
Pydantic 的欄位名直接當 ORM 的建構參數，兩者被綁死。

**後果**：

- DB 欄位改名 = API 破壞，而且沒有任何一層會在 review 時擋下來。
- 序列化發生在 route，此時 session 可能已經關閉或觸發 lazy load。
- 11 條 `service → schema` 是這個設計的鏡像：業務層為了收 `payload` 而 import
  Pydantic（`connection_service.py`、`project_service.py`、`publish_service.py`…）。

**判定**：❌ 中等價值。這是 #1 和 #3 完成後自然會浮現的一步。

---

### 落差 7｜例外沒有分層，錯誤訊息散在 route

**現況**：33 個 `*Error` 類別定義在 `app/services/`（不是 domain），
77 處 `HTTPException` 散在 route：

| route | `HTTPException` 數 |
|---|---:|
| `releases.py` | 30 |
| `projects.py` | 17 |
| `connections.py` / `deployments.py` | 各 7 |
| `auth.py` / `drone.py` | 各 4 |
| 其餘 6 檔 | 8 |

`releases.py` 為了把例外翻譯成 HTTP 狀態碼，光 import 就從 **7 個 service 模組拉進 16 個
例外類別**，再加 4 個 Drone 例外（L13-65，共 53 行 import）。錯誤訊息字串直接內嵌在
`detail=` 裡，同一句話在多個 route 重複。

**判定**：⚠️ 低價值但也低成本。dddpy 的 `presentation/api/{aggregate}/error_messages/`
可以在任何階段順手引入。

---

### 落差 8｜Transaction 邊界散落

**規範**：UseCase 是 transaction 邊界，`execute()` 進出各一次。

**現況**：43 處 `.commit()` 分佈在 12 個 service 檔：

```
release_orchestrator.py  7    project_service.py    4    publish_orchestrator.py  3
release_service.py       5    auth_service.py       4    deployment_service.py    3
notification_service.py  5    recovery_service.py   3    connection_service.py    3
schedule_service.py      4    workflow_service.py   1    publish_service.py       1
```

因為 service 會互相呼叫（31 條邊），一次 HTTP 請求可能經過兩三個 service，
每個都可能 commit——巢狀呼叫時哪一次 commit 才是邊界，要讀呼叫鏈才知道。

`release_service.create()`（L45-62）是個好例子：它 `flush()` → 呼叫
`workflows.create_for_release()` → 才 `commit()`，兩個 aggregate 在同一個
transaction，而這個約定只存在於這段程式碼的順序裡。

**判定**：❌ 中等價值。引入 UseCase 層時會一併解決。

---

## 遷移前先看清楚：現有的防護網

重構這種規模的東西，能不能做取決於測試接在哪一層。實測結果：

| 指標 | 數字 | 對遷移的意義 |
|---|---:|---|
| 測試總行數 | 6,561 | 夠厚 |
| 走 HTTP（`TestClient`）的測試檔 | 16 / 21 | ✅ 內部重構不會動到 |
| 直接 `import app.services` 的測試檔 | 11 / 21 | ⚠️ 改 service 形狀就會紅 |
| coverage 門檻 | 85%（branch） | 硬約束，遷移中不得下降 |

**好消息**：`test_releases.py`(26)、`test_project_registry.py`(42)、
`test_scheduling_notifications.py`(41)、`test_n_component_release.py`(24)
這些主力測試都是打 HTTP endpoint，只要 API 契約不變就完全不受影響。

**要注意的 11 個檔案**（重構 service 時會一起改）：

```
test_audit_trail.py            → ReleaseOrchestrator, DroneBuildService
test_concurrency_guards.py     → ReleaseOrchestrator, DeploymentService, DuplicatePromotionError
test_project_scope.py          → PublishService, DeploymentService, promote_stage/wait_stage
test_recovery.py               → RecoveryService
test_upstream_resilience.py    → 各 client
test_n_component_release.py    → FixedDroneClients / FixedGiteaClients
test_project_registry.py       → connection_service, project_service（模組層 patch）
（其餘 4 檔為輕度引用）
```

`test_project_registry.py` 在測試函式內部 `from app.services import connection_service`
再 monkeypatch 模組屬性（L157、175、192、209、525、541、562）——**這種寫法會綁死模組路徑**，
是遷移時最容易碎的地方。

---

## 分階段遷移路徑

原則：**每一階段自己就是一個可合併、可回退的 commit，中途停下來程式碼也不會比現在差。**

### Phase 0｜抽出 Domain 層的 Value Object（純搬移）✅ 已完成

把 14 個 enum 和 `utc_now` 搬到 `app/domain/`，`app/db/models/` 反過來從那裡 import。

```
app/domain/shared/time.py                    ← utc_now
app/domain/release/value_objects/status.py   ← ReleaseStatus
app/domain/orchestration/value_objects/      ← Component, ReleaseMode, DeploymentStatus,
                                               BundleStatus, PublishStatus,
                                               PublishRecordStatus, WorkflowStage,
                                               WorkflowEventType, ActorSource
app/domain/registry/value_objects/           ← ConnectionKind, ConnectionVerifyStatus
app/domain/scheduling/value_objects/         ← ScheduleStatus, EmailDeliveryStatus
```

**注意**：`ACTIVE_PROMOTION_STATUSES` / `ACTIVE_PROMOTION_SLOT` /
`ACTIVE_PROMOTION_PREDICATE`（`orchestration.py:84`、`101`、`109`）**留在 db/models**。
它們是 SQL 索引的定義，不是領域概念——搬走反而會讓「索引和檢查同源」這件事變模糊。

- **改動**：14 個 enum 定義搬家；實際改到 22 個 `app/` 檔案、5 個 `tests/` 檔案、
  4 個 model 檔與 `app/db/models/__init__.py`
- **行為改變**：零
- **驗收結果**：`uv run quality` 全綠（247 passed，coverage 89.53%）；
  `schema → db` 5 → **0**，`presentation → db` 18 → **12**（只剩 `get_db`），
  `service → db` 37 → **26**（比預期多降 11 條）
- **風險**：低。唯一要小心的是 `WorkflowStage` 被 BPMN 和前端依賴，**值不能改**，只搬位置。
- **成本**：半天

### Phase 1｜集中 DI wiring（純搬移，可與 Phase 0 並行）✅ 已完成

建 `app/infrastructure/di/injection.py`，把 8 個 route 檔裡的工廠函式全部搬進去，
route 只 `from app.infrastructure.di.injection import ...`。

- **驗收結果**：`grep "from app.api.routes" app/api/routes/` 回傳 **0 筆**（原本 6 筆）；
  `uv run quality` 全綠（247 passed，coverage 89.50%）
- **附帶好處**：`drone.py` 不再身兼 DI 容器；順手發現並移除一組重複工廠
- **風險**：低。`Depends` 的解析是執行期的，搬移後要跑一次完整測試確認沒有循環 import。
- **成本**：半天

### Phase 2｜垂直切片：把 v1 `Release` 做成完整的四層 ✅ 已完成

**為什麼選 Release 當第一個**：

| 條件 | Release（v1 approval） | Deployment / Bundle |
|---|---|---|
| service 行數 | 210 | 704 / 480 |
| 依賴其他 service | 1（workflow_service） | 3～6 |
| 外部 API 呼叫 | 無 | Drone + Gitea |
| 測試進入點 | 全部走 HTTP | 有直接 import service 的 |
| 狀態機 | 4 個轉移，教科書等級 | 複雜、含逾時與重試 |

Release 是唯一「小到能一次看完、又完整包含狀態機」的 aggregate，而且 README 說它是
**保留的 v1 API，行為凍結**——最適合當白老鼠。

目標結構：

```
app/domain/release/
    entities/release.py            Release（帶 approve/reject/start/finish，純 Python）
    value_objects/status.py        ReleaseStatus（Phase 0 已就位）
    repositories/release_repository.py   ReleaseRepository(ABC)
    exceptions/                    InvalidReleaseTransitionError, ReleaseNotFoundError
app/infrastructure/sqlite/release/
    release_dto.py                 to_entity() / from_entity()
    release_repository.py          SqlAlchemyReleaseRepository（CAS 留在這裡）
app/usecase/release/
    create_release_usecase.py      approve_release_usecase.py
    reject_release_usecase.py      start_deployment_usecase.py
    finish_deployment_usecase.py   list_releases_usecase.py
app/presentation/api/release/
    handlers/  schemas/  error_messages/
```

**最關鍵的一件事**——CAS 不能弄丟。Repository interface 必須長成這樣：

```python
class ReleaseRepository(ABC):
    @abstractmethod
    def save_transition(self, release: Release, *, expected: ReleaseStatus) -> bool:
        """回傳 False 代表狀態已被別人改掉（rowcount == 0）。"""
```

實作照舊 `update(Release).where(status == expected)`。UseCase 拿到 `False` 就
raise `InvalidReleaseTransitionError`。

**驗收結果**：

1. ✅ `tests/test_releases.py`、`test_workflows.py` 一行都沒改，全綠
2. ✅ `tests/test_release_domain.py`——19 個測試、0.05 秒、零資料庫，
   另含兩個結構不變式（domain 不得 import framework 或外層）
3. ✅ 並行測試加在 `tests/test_concurrency_guards.py`，兩執行緒核准只有一個贏，
   連跑 10 次穩定
4. ✅ coverage 90.01%（原本 89.50%）；267 passed（原本 247）
5. ✅ OpenAPI 67 個端點與 `main` 逐字相同

**實際遇到的風險**：`release_service.create()` 和 `workflow_service` 共用
transaction，這題的答案是 `UnitOfWork`——repository 只 add/flush/execute，
由 UseCase 決定何時 commit。`WorkflowService` 則發現它只讀 `release.id` 與
`release.status`，所以直接改成接 domain entity，不需要另外包一層。

**成本**：2～3 天。**這一階段的產出同時是後續所有 aggregate 的樣板。**

### Phase 3｜依 Phase 2 的樣板推進其餘 aggregate

**順序已依實測修正**（見上方「Phase 3 的順序被實測推翻了」）：
`Connection` ✅ → `Project`/`Component` → orchestration。

orchestration 那一刀不拆成四份：`Deployment`、`ReleaseBundle`、`PublishRecord`
與 `DeploymentSchedule` 共用同一組欄位（發布狀態就長在 `deployments` 表上），
拆開只會把跨 aggregate 寫入變成跨層寫入。

搬 `Deployment` 時務必保留兩件事：

- `uq_deployments_active_promotion` 與 `assert_not_duplicate` 的分工
  （一個是保證、一個是友善訊息）——這個註解要跟著搬到 repository 實作裡
- `claim_for_promotion` 的 `WHERE status = 'WAITING'` claim，同 Phase 2 的 CAS 處理

**每個 aggregate 一個 commit，每個 commit 都跑完整 `uv run quality`。**

### Phase 4｜收尾（可選）

- `error_messages/` 集中 77 處 `HTTPException` 的字串
- Transaction 邊界統一由 UseCase 界定，service 內部不再 `commit()`
- `Settings` 從業務層退出，改由 DI 注入具體的設定值物件

---

## 明確建議「不要做」的四件事

1. **不要把資料庫的不變量搬進 domain。**
   `uq_deployments_active_promotion`、`workflow_events` 的 append-only trigger、
   各種 `WHERE status = X` 的 CAS——這些在應用層做不到等價的事。
   教科書 DDD 在這裡是錯的，現有設計是對的。

2. **不要重構 BPMN / SpiffWorkflow 那一塊。**
   `WorkflowStage` 的值被序列化的 workflow instance 和前端 viewer 共同依賴，
   改 ID 需要的是 migration 不是重構。

3. **不要一次全改。**
   `service → service` 有 31 條邊，同時動兩個 aggregate 就會出現「改到一半兩邊都不能編譯」
   的狀態。Phase 2 沒有做完並合併之前，不要開 Phase 3。

4. **不要為了層數而拆 `ComponentRegistry` 和 `upstream_clients`。**
   `upstream_clients.py` 已經用 `Protocol` 做了正確的依賴反轉
   （`DroneClients` / `GiteaClients`，L26-31），它是這份程式碼裡**最接近 DDD 的部分**。
   Phase 3 應該以它為範本，而不是改寫它。

---

## 附錄：現況 → 目標對照

| 現在 | 目標層 | 備註 |
|---|---|---|
| `app/db/models/*.py` 的 enum | `app/domain/*/value_objects/` | Phase 0 |
| `app/db/models/release.py:utc_now` | `app/domain/shared/time.py` | 13 處 import 一起改 |
| `app/db/models/*.py` 的 ORM 類別 | `app/infrastructure/sqlite/*/`_dto.py | 保留 `__table_args__` 原樣 |
| `app/db/session.py` | `app/infrastructure/sqlite/` | |
| `app/services/*_service.py` 的規則 | `app/domain/*/entities/` | |
| `app/services/*_service.py` 的查詢 | `app/infrastructure/sqlite/*/repository.py` | |
| `app/services/*_orchestrator.py` | `app/usecase/*/` | 一個公開方法一個 UseCase |
| `app/services/*Error` (33 個) | `app/domain/*/exceptions/` | |
| `app/integrations/` | `app/infrastructure/` | 已經是正確的方向，改路徑即可 |
| `app/schemas/` | `app/presentation/api/*/schemas/` | |
| `app/api/routes/` | `app/presentation/api/*/handlers/` | |
| route 裡的 `Depends` 工廠 | `app/infrastructure/di/injection.py` | Phase 1 |
| `app/workflows/bpmn/` | 原地不動 | |

## 附錄：下一步

Phase 0 到 Phase 2 都在 `refactor/ddd-phase-0-1` 上，每個 commit 都可以獨立 review
與回退，而且 API 契約自始至終沒有動過。

Phase 2 要回答的兩題都有答案了，而且答案寫成了可以照抄的程式碼：

- **規則與並行怎麼分工** → 轉移方法回傳前置狀態，repository 拿去當 CAS 條件
  （`Release.approve` + `SqlAlchemyReleaseRepository.save_transition`）
- **transaction 誰結束** → `UnitOfWork`，repository 不 commit
  （`app/domain/shared/unit_of_work.py`）

**Phase 3 建議順序**：`Publish` → `Schedule` → `Deployment` → `Bundle`。
`Deployment` 放最後，因為它最大（704 行）也被最多人依賴，而且它有兩個 CAS
要照 Phase 2 的方式處理：`claim_for_promotion` 的 `WHERE status = 'WAITING'`，
以及 `uq_deployments_active_promotion` 那個部分唯一索引。後者是**索引**不是
CAS，照 Phase 0 的判斷留在 `app/db/models/`——搬進 domain 會弄壞它。

Phase 3 之前沒有新問題要想，剩下的是重複勞動——每個 aggregate 一個 commit，
每個 commit 跑一次 `uv run quality` 加一次 OpenAPI 比對。
