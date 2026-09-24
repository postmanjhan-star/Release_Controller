# 前端效能審查：Vercel React Best Practices 全面對照

審查對象：`frontend/src/`（React 19.1.1 + Vite 8 + Redux Toolkit 2.12，SPA）
審查基準：Vercel React Best Practices，8 大類共 70 條
日期：2026-09-04

## 實作進度

六個階段全部完成，在分支 `perf/vercel-react-review` 上，每階段一個 commit，
每階段都跑過 `npm run quality`（typecheck / lint / prettier / 51 個測試 /
12 項結構不變式 / build / smoke）。

| 項目 | 狀態 | commit |
|------|------|--------|
| 1-2 熱路徑：`formatTime`、`escapeHtml` | ✅ | `perf: remove per-call allocations from the render hot path` |
| 6-8 `workflowStatusGroup`、`componentLabel`、events 分組 | ✅ | 同上 |
| 10 `workflowHistoryItems` 排序改字典序 | ✅ | 同上 |
| 3 `elements.detail` listener 累加 | ✅ | `fix: bind the component-row actions once per render` |
| 4 三個健康檢查改並行 | ✅ | `perf: start the health probes and the data load together` |
| 5 `main.tsx` 移除重複 bpmn CSS | ✅ | `perf: keep the bpmn stylesheets out of the entry bundle` |
| 11 長列表 `content-visibility` | ✅ | 同上 |
| 13 移除 `lucide` | ✅ | 同上 |
| 9 `bpmn-js` 動態 import | ✅ | `perf: load bpmn-js on first use instead of with the controller` |
| 12 登出／再登入 teardown | ✅ | `fix: stop the controller when the shell unmounts` |
| 10(B) `currentHistory()` 快取層 | ⏸ 未做 | 收益有限，失效時機不好抓 |
| 14 localStorage schema 版本 | ⏸ 未做 | 現有的形狀檢查已經夠用 |

### 量到的 bundle 變化

| | 之前 | 之後 |
|---|---|---|
| entry JS | 233.90 kB (72.75 gz) | 233.88 kB (72.76 gz) |
| entry CSS | 54.41 kB (10.99 gz) | **29.16 kB (6.44 gz)** |
| controller chunk（登入後的阻塞路徑） | 260.62 kB (72.40 gz) | **67.99 kB (18.15 gz)** |
| bpmnViewer chunk（新，非阻塞） | — | 192.95 kB (54.97 gz) + 25.36 kB CSS |

登入後真正擋住介面的那塊從 **72.40 kB gzip 降到 18.15 kB gzip**。
bpmn-js 仍然會在 releases 檢視載入（那裡本來就會畫定義預覽圖），
差別是它不再擋著事件綁定與資料載入。

### 五個新測試，每個都對舊程式碼驗證過會失敗

| 測試 | 舊程式碼的行為 |
|------|----------------|
| 看過多個專案後，元件按鈕仍只送出一次請求 | 送出 **5 次**相同的 reorder |
| 健康檢查沒回應也不擋住資料載入 | **完全不渲染** |
| 重新登入後 controller 重新綁定到新的 DOM | build 列 **0 筆**，介面是死的 |
| 登出後停止輪詢 | 計時器**從未被清除** |
| signing out stops the controller | `teardown` **從未被呼叫** |

### 順手補的結構不變式

`tests/ui-contract.cjs` 原本斷言 `controller.ts` 內含
`from 'bpmn-js/lib/NavigatedViewer'`。改成斷言 `bpmnViewer.ts` 匯出
NavigatedViewer，**而且** `controller.ts` 只能用動態 import 取用它——
「不可以綁進阻塞 chunk」這個性質沒有任何行為測試會發現被破壞。

### 未驗證的部分

`npm run test:e2e` 沒有跑到：這個環境的 egress 允許 npm registry，但不允許
Playwright 的瀏覽器下載 CDN。合併前請在本機或 CI 跑一次 `npm run quality:ci`。

## 這個專案的特殊架構

`App.tsx` 只負責渲染一層靜態 shell 與 dialog，真正的互動全部由 `controller.ts`（2330 行）
以命令式 DOM 操作接手（`document.querySelector` + `innerHTML` + 事件委派）。
檔案開頭自己也寫了「Transitional imperative adapter」。

這件事決定了整份對照的重心：

- **re-render 類 15 條幾乎全部不適用** — React 幾乎沒有狀態，`App` 一輩子只重繪 3～4 次。
- **server 類 10 條全部不適用** — 沒有 Next.js、沒有 RSC、沒有 SSR。
- **真正會痛的是 async waterfall、bundle、以及 `controller.ts` 裡的 JS 熱路徑**，
  因為那裡有一個每 5 秒跑一次的輪詢迴圈（`controller.ts:2321`）。

---

## 結論摘要：依影響度排序的前 6 項

| # | 問題 | 位置 | 對應規則 | 判定 |
|---|------|------|----------|------|
| 1 | 專案詳情每重繪一次就疊加一個 click listener，導致重複送出 API 請求 | `controller.ts:1215` | `client-event-listeners` | 確認 |
| 2 | 啟動時三個健康檢查序列化 await，資料載入被卡在後面 | `controller.ts:2291-2319` | `async-parallel` | 確認 |
| 3 | `bpmn-js` 靜態 import 進 controller chunk，CSS 更被塞進 entry bundle | `controller.ts:3-5`、`main.tsx:5-6` | `bundle-dynamic-imports`、`bundle-conditional` | 確認 |
| 4 | `formatTime` 每次呼叫都 `new Intl.DateTimeFormat`（輪詢熱路徑） | `controller.ts:243` | `js-cache-function-results` | 確認 |
| 5 | `escapeHtml` 每次呼叫都 `createElement`（72 個呼叫點，多在 `.map()` 內） | `controller.ts:233` | `js-cache-function-results` | 確認 |
| 6 | 登出後 `setInterval` 不停、再登入時 controller 完全失效 | `controller.ts:2281,2321`、`App.tsx:13-24` | `advanced-init-once` | 確認 |

---

## 一、Eliminating Waterfalls（CRITICAL，6 條）

| 規則 | 判定 |
|------|------|
| `async-cheap-condition-before-await` | ✅ 通過 |
| `async-defer-await` | ✅ 通過 |
| `async-parallel` | ❌ **違反** |
| `async-dependencies` | ⚠️ 註解與實作矛盾 |
| `async-api-routes` | ❌ **違反**（同 `async-parallel`） |
| `async-suspense-boundaries` | ➖ 不適用 |

### 1.1 ❌ 啟動時三個健康檢查是序列化的（`controller.ts:2291-2316`）

```ts
try { await api('/health'); ... } catch { ... }              // 第 1 趟
try { const drone = await api(`${API}/drone/status`); ... }  // 第 2 趟，等第 1 趟結束
try { const gitea = await api(`${API}/gitea/status`); ... }  // 第 3 趟，等第 2 趟結束
await Promise.allSettled([loadProjects(), loadHistory()]);   // 真正的資料，等前 3 趟全部結束
```

這三個檢查彼此完全獨立，也和 `loadProjects()` / `loadHistory()` 無關。
`/api/v1/drone/status` 和 `/api/v1/gitea/status` 是後端去打外部 Drone/Gitea 的檢查，
延遲通常不低——三趟串起來就是登入後畫面空白的主要來源。

**修法**：健康檢查各自 fire-and-forget，資料載入立刻開始。

```ts
// 立刻發出全部請求，誰先回誰先更新
const health = (async () => {
  try {
    await api('/health');
    setHealth(elements.health, true, 'Service healthy');
  } catch { setHealth(elements.health, false, 'Service unavailable'); }
})();

const drone = (async () => {
  try {
    const body = await api(`${API}/drone/status`);
    if (body.status !== 'ok') throw new Error('Drone unavailable');
    setHealth(elements.droneHealth, true, 'Drone available');
  } catch { setHealth(elements.droneHealth, false, 'Drone unavailable'); }
})();

const gitea = (async () => { /* 同上 */ })();

await Promise.allSettled([loadProjects(), loadHistory(), health, drone, gitea]);
```

`setHealth` 抽成小工具即可（三段 `classList.add` + `querySelector('span:last-child').textContent`
本來就是同一段程式碼抄三次）。

### 1.2 ⚠️ 行 2317 的註解和行 2319 的實作不一致

```ts
// Projects first: which components exist, and their repositories and target,
// all come from the selected one.
await Promise.allSettled([loadProjects(), loadHistory()]);
```

註解說「Projects first」，程式卻是並行的。`loadHistory()` 最終會走到 `renderBundleDetail()`
→ `componentLabel()`，而 `componentLabel()` 讀的是 `state.project`——那是 `loadProjects()`
正在填的東西。結果是首次繪製時元件名稱可能落到 fallback（把 key 轉成 "Backend" 這種樣子），
專案載完之後才會在下一次重繪修正。

不是嚴重 bug（`componentLabel` 的 fallback 設計就是為了這個），但註解描述的保證並不存在。
要嘛改註解，要嘛真的照註解做：

```ts
await loadProjects();          // 元件名稱／目標先就位
await loadHistory();
```

代價是多一個往返。折衷做法是維持並行，但在 `loadProjects()` resolve 後補一次 `renderHistory()`。

### 1.3 ✅ 做得好的部分

- `controller.ts:2322` — `if (document.hidden) return;` 在任何 await 之前，
  正是 `async-cheap-condition-before-await` 要的形狀。
- `controller.ts:1961`、`1974` — `TERMINAL.has(status)` 先擋掉終態記錄，才發請求。
- `controller.ts:1284/1291/1298/1308/1485/1494` — record 和 events 一律 `Promise.all` 並行取。
- `controller.ts:1357` — `loadSettingsData()` 把 projects 和 connections 並行取。
- `controller.ts:510/548` — 各 component 的 builds 用 `Promise.allSettled` 並行載入。

這一類的觀念其實掌握得不錯，唯一漏掉的就是 `initialize()` 那三段。

---

## 二、Bundle Size Optimization（CRITICAL，6 條）

| 規則 | 判定 |
|------|------|
| `bundle-barrel-imports` | ✅ 通過 |
| `bundle-analyzable-paths` | ✅ 通過 |
| `bundle-dynamic-imports` | ❌ **部分違反** |
| `bundle-defer-third-party` | ➖ 不適用（沒有 analytics） |
| `bundle-conditional` | ❌ **違反** |
| `bundle-preload` | ⚠️ 可加分 |

### 2.1 ❌ `bpmn-js` 靜態綁在 controller chunk 上（`controller.ts:3`）

```ts
import NavigatedViewer from 'bpmn-js/lib/NavigatedViewer';
```

`controller.ts` 本身已經是動態載入的（`App.tsx:17`，這點做得對），但 `bpmn-js` 是靜態 import，
所以它整包被打進 controller chunk。結果是：**登入成功後，整個操作介面要等 bpmn-js 下載完才會動**，
即使使用者根本沒點開任何一筆 workflow。

`NavigatedViewer` 只在 `renderBpmnProgress()`（行 1026）用得到。

**修法**：

```ts
let NavigatedViewerCtor: any = null;
async function loadBpmnViewer() {
  NavigatedViewerCtor ??= (await import('bpmn-js/lib/NavigatedViewer')).default;
  return NavigatedViewerCtor;
}

// renderBpmnProgress 內，行 1026 附近：
const Viewer = await loadBpmnViewer();
if (renderToken !== bpmnRenderToken) return;   // 注意：多了一個 await，要重新檢查 token
const viewer: any = new Viewer({ container });
```

⚠️ 這個改動多插了一個 await 點，`bpmnRenderToken` 的檢查要跟著補一次，否則使用者快速切換
記錄時會出現舊 viewer 蓋掉新 viewer 的競態。

### 2.2 ❌ bpmn-js 的 CSS 被放進 entry bundle（`main.tsx:5-6`）

```ts
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";
```

這兩行在 **entry**（`main.tsx`）裡，代表連還沒登入、只看到 `LoginScreen` 的訪客
也會下載 diagram-js 樣式表和 bpmn 字型 CSS（後者還會再拉 woff/ttf 字型檔）。

而且這兩行在 `controller.ts:4-5` **重複了一次**。既然 controller 是動態載入的，
留 controller 那份就好，`main.tsx` 這兩行直接刪掉——CSS 會跟著 controller chunk 一起延後載入。

### 2.3 ⚠️ `lucide` 是未使用的相依（`package.json:24`）

`grep -rn "lucide" src/` 沒有任何結果。Vite 會 tree-shake 掉，所以 **bundle 大小不受影響**，
但它仍然佔著 install 時間和依賴稽核面。確認沒有計畫要用的話就移除。

### 2.4 ⚠️ 可加分：hover 時預載 controller（`bundle-preload`）

登入按鈕（`App.tsx:112-118`）是通往 controller 的唯一入口。加上：

```tsx
<a
  className="button primary login-button"
  href="/api/v1/auth/login"
  onMouseEnter={() => void import("./controller")}
  onFocus={() => void import("./controller")}
>
```

使用者從按下登入到 OAuth 轉回來這段時間，chunk 已經在下載了。

### 2.5 ✅ 做得好的部分

- `bpmn-js/lib/NavigatedViewer` 是深層路徑 import，不是 barrel（`bundle-barrel-imports` ✅）。
- `import("./controller")` 是靜態可分析字串，Vite 能正確切出 chunk（`bundle-analyzable-paths` ✅）。

---

## 三、Server-Side Performance（HIGH，10 條）

➖ **全部不適用**。這是 Vite SPA，後端是 FastAPI，不存在 RSC / Server Actions /
`React.cache()` / `after()` 這些概念。

`server-auth-actions`（驗證 server action）的精神在後端 FastAPI 那側，不在本次審查範圍。

---

## 四、Client-Side Data Fetching（MEDIUM-HIGH，4 條）

| 規則 | 判定 |
|------|------|
| `client-swr-dedup` | ✅ 通過（手寫版） |
| `client-event-listeners` | ❌ **違反（本次最嚴重）** |
| `client-passive-event-listeners` | ➖ 不適用 |
| `client-localstorage-schema` | ⚠️ 缺版本欄位 |

### 4.1 ❌ 最嚴重：`renderProjectDetail` 每次重繪疊加一個 listener（`controller.ts:1215`）

```ts
function renderProjectDetail(project: Project) {
  const changed = patchDetail(`...`);
  if (!changed) return;
  document.querySelector('#edit-project').addEventListener(...);      // 1211：新節點，OK
  // ...
  elements.detail.addEventListener('click', (event: any) => {          // 1215：常駐節點，累加！
    // data-move-component / data-edit-component / data-delete-component
  });
}
```

`patchDetail()`（行 175-181）做的是 `elements.detail.innerHTML = html`。
**子節點被換掉，但 `elements.detail` 這個容器本身從頭到尾是同一個節點**，
所以掛在它身上的 listener 一個都不會消失。

行 1211-1214 那四個是掛在 `innerHTML` 產生的**新**節點上，每次重繪都是全新節點，正確。
只有行 1215 掛在常駐容器上。

`changed` 只擋住「內容完全相同」的重繪；切換到另一個專案時 HTML 一定不同，
所以 **看過幾個專案，就累積幾個 handler**。

具體後果（看過 3 個專案後，點一下元件列的按鈕）：

| 按鈕 | 後果 |
|------|------|
| ↑ / ↓ 調整順序 | 送出 **3 次內容相同的** `POST .../components/reorder`，接著跑 3 次 `refreshProjectsView`（每次 = 1 個 projects GET + N 個 builds GET） |
| Edit | `openComponentDialog` 被呼叫 3 次，`showModal()` 對已開啟的 dialog 重複呼叫 |
| Remove | 併發送出 3 個相同的 DELETE，第 2、3 個很可能拿到 404 → 冒出錯誤 toast |

順序不會被弄亂（三個 handler 在同一次事件派發中同步啟動，讀到的都是同一份未更新的
`currentDetailRecord`，算出來的 `component_ids` 完全相同），所以這是**重複寫入 + 重複拉取**，
不是資料損毀。但「按刪除卻跳錯誤訊息」使用者一定會看到。

**這個模組自己知道正確做法**——`bindEvents()` 行 2188 的註解寫著：

> The zoom controls live inside the detail markup, so bind them once here rather
> than on every BPMN render.

行 2190 就照這個原則把 BPMN 縮放的委派 listener 綁在 `elements.detail` 上，**綁一次**。

**修法**：把行 1215-1231 整塊搬進 `bindEvents()`，和行 2190 那個並列。
handler 內本來就是讀 `currentDetailRecord`，所以搬家不需要改邏輯，
只要加一道「目前確實在專案詳情」的守門：

```ts
// bindEvents() 內，行 2201 之後
elements.detail.addEventListener('click', (event: any) => {
  if (!(event.target instanceof Element)) return;
  if (state.view !== 'projects') return;
  const project = currentDetailRecord as Project | null;
  if (!project?.components) return;

  const move = event.target.closest('[data-move-component]');
  if (move) {
    void moveComponent(project, move.dataset.moveComponent, Number(move.dataset.direction));
    return;
  }
  const edit = event.target.closest('[data-edit-component]');
  if (edit) {
    const component = project.components.find((item) => item.key === edit.dataset.editComponent);
    if (component) openComponentDialog(project, component);
    return;
  }
  const remove = event.target.closest('[data-delete-component]');
  if (remove) void deleteComponent(project, remove.dataset.deleteComponent);
});
```

然後 `renderProjectDetail` 只留行 1210-1214。

**順手檢查**：行 1264（`renderLegacyDetail`）用 `elements.detail.querySelectorAll(...)`
把 listener 掛在**子節點**上，那是安全的。行 1083/1084/1104/1105/1119/1135/1249-1251
也都是子節點。**只有 1215 這一處有問題。**

### 4.2 ⚠️ localStorage 沒有版本欄位（`controller.ts:31-32`）

```ts
const SCHEDULE_RECIPIENTS_STORAGE_KEY = 'release-controller.schedule-recipients';
const PROJECT_STORAGE_KEY = 'release-controller.project';
```

key 有 namespace（好），但存的值沒有 schema 版本。
`savedScheduleRecipients()`（行 1581）已經有 `Array.isArray` 的形狀檢查，
所以目前不會炸；但哪天要改成存 `{ email, enabled }` 物件陣列，
舊資料就得靠猜。建議存 `{ v: 1, values: [...] }`，讀的時候比對 `v`。

低優先，現有的防禦已經夠用。

### 4.3 ✅ 做得好的部分

- **`client-swr-dedup` 手寫實作**（行 471-484）：`projectsLoad` 這個 in-flight promise
  就是 SWR 去重的核心機制，註解也把「為什麼」解釋得很清楚。這寫得很好。
- **事件委派**：`elements.history`（2126）、`#component-grid` 的 click（2144）和 change（2172）
  都是綁在常駐容器上一次，正是 `client-event-listeners` 要的形狀。
- `client-passive-event-listeners` 不適用——全專案沒有 scroll / touch / wheel listener。

---

## 五、Re-render Optimization（MEDIUM，15 條）

因為狀態幾乎全在 React 之外，這 15 條裡有 13 條**不適用**。
`App` 只在 `auth` 變動時重繪，整個 session 大約 3～4 次。

| 規則 | 判定 |
|------|------|
| `rerender-no-inline-components` | ✅ 通過 |
| `rerender-functional-setstate` | ➖ 不適用（沒有 `useState`） |
| `rerender-lazy-state-init` | ➖ 不適用 |
| `rerender-derived-state-no-effect` | ✅ 通過 |
| `rerender-defer-reads` | ⚠️ 輕微 |
| 其餘 10 條（memo / transitions / deferred value / refs / split hooks…） | ➖ 不適用 |

### 5.1 ⚠️ `App` 訂閱整個 auth slice（`App.tsx:874`）

```ts
const auth = useAppSelector((state) => state.auth);
```

嚴格說違反 `rerender-defer-reads`（訂閱了比需要更多的東西）。
但 `App` 是唯一的消費者、整個 slice 的欄位它幾乎都用到、而且 auth 一輩子變幾次，
**實務上完全不值得改**。列在這裡只是為了對照完整。

真的要照規則寫的話：

```ts
const status = useAppSelector((s) => s.auth.status);
const authenticated = useAppSelector((s) => s.auth.authenticated);
const user = useAppSelector((s) => s.auth.user);
```

可讀性反而變差。**建議維持現狀。**

### 5.2 ✅ 做得好的部分

- 所有元件（`TopBar`、`LoginScreen`、`LaunchPanel`、各 Dialog）都定義在模組層，
  沒有一個定義在另一個元件內部（`rerender-no-inline-components` ✅）。
- `App.tsx:875-877` 的 `useEffect` 是真正的資料載入副作用，不是「用 effect 算 derived state」
  的反模式（`rerender-derived-state-no-effect` ✅）。
- `HISTORY_TABS`（`App.tsx:6`）已經提到模組層，不是每次 render 重建
  （`rerender-memo-with-default-value` 的精神 ✅）。

---

## 六、Rendering Performance（MEDIUM，11 條）

| 規則 | 判定 |
|------|------|
| `rendering-svg-precision` | ✅ 通過 |
| `rendering-script-defer-async` | ✅ 通過 |
| `rendering-hoist-jsx` | ⚠️ 輕微 |
| `rendering-conditional-render` | ⚠️ 輕微 |
| `rendering-content-visibility` | ❌ **可改善** |
| `rendering-hydration-no-flicker` / `-suppress-warning` | ➖ 不適用（無 SSR） |
| `rendering-activity` / `-usetransition-loading` / `-resource-hints` / `-animate-svg-wrapper` | ➖ 不適用 |

### 6.1 ❌ 長列表沒有 `content-visibility`

兩個地方會長：

- **history list**（`controller.ts:762`）：`workflows` 檢視把 releases、deployments、
  schedules、legacy 四種各 `limit=100` 全部串起來，最壞情況 400 列，
  而且每列還帶一段 `workflow-lifecycle` 的巢狀 markup（行 707-710）。
- **event list**（`controller.ts:1081`）：一筆 bundle 的所有 workflow event 全部展開，
  而且**每 5 秒重繪一次**。

在 `styles.css` 加：

```css
.history-row,
.event-list > li {
  content-visibility: auto;
  contain-intrinsic-size: auto 72px;   /* 量一下實際列高再填 */
}
```

瀏覽器就會跳過視窗外那些列的排版與繪製。`contain-intrinsic-size` 一定要給，
否則捲軸長度會跳動。

### 6.2 ⚠️ `App.tsx` 的靜態 dialog 每次 render 重建（`rendering-hoist-jsx`）

`ConfirmationDialog`、`PromoteDialog`、`ScheduleDialog`、`RecipientDialog`、
`PublishDialog`、`LegacyDialogs`、`SettingsDialogs`、`LaunchPanel`、`HistoryWorkspace`
全部**不吃 props**，是純靜態 JSX，但每次 `App` 重繪都會重新建立整棵 element tree。

照規則應該提到模組層：

```tsx
const DIALOGS = (
  <>
    <ConfirmationDialog />
    <PromoteDialog />
    {/* ... */}
  </>
);
```

**但 `App` 一個 session 只重繪 3～4 次，實際收益接近零。** 純粹為了對照完整而列出，
不建議為此改動。

### 6.3 ⚠️ `&&` 條件渲染（`App.tsx:101`）

```tsx
{(error || callbackError) && (
  <div className="login-error" role="alert">{error || callbackError}</div>
)}
```

`rendering-conditional-render` 建議用三元而非 `&&`，因為 `&&` 碰到 `0` 或 `''`
會把值本身渲染出來。這裡兩個運算元都是 `string | null`，`null || null` 得到 `null`，
React 不渲染——**目前是安全的**。但如果哪天型別變成 number，就會踩到。

```tsx
{error || callbackError ? (
  <div className="login-error" role="alert">{error || callbackError}</div>
) : null}
```

### 6.4 ✅ 做得好的部分

- `bpmnTaskIcon()`（行 992-994）的內嵌 SVG 座標全是整數，沒有 `12.0000001` 這種
  浮點雜訊（`rendering-svg-precision` ✅）。
- `index.html` 用 `<script type="module">`，本身就是 defer 語意
  （`rendering-script-defer-async` ✅）。

---

## 七、JavaScript Performance（LOW-MEDIUM，14 條）

這一類在本專案的權重被架構放大了：所有渲染都是手寫字串拼接，
而且有一個 5 秒輪詢迴圈會反覆走過同樣的程式碼。

| 規則 | 判定 |
|------|------|
| `js-cache-function-results` | ❌ **違反（2 處，熱路徑）** |
| `js-index-maps` | ❌ **違反（3 處）** |
| `js-set-map-lookups` | ❌ **違反** |
| `js-combine-iterations` | ⚠️ 可改善 |
| `js-hoist-regexp` | ⚠️ 輕微 |
| `js-tosorted-immutable` | ✅ 通過 |
| `js-flatmap-filter` | ✅ 通過 |
| `js-early-exit` | ✅ 通過 |
| `js-cache-storage` | ✅ 通過 |
| `js-batch-dom-css` / `js-cache-property-access` / `js-length-check-first` / `js-min-max-loop` / `js-request-idle-callback` | ➖ 不適用或影響可忽略 |

### 7.1 ❌ `formatTime` 每次呼叫都建構 `Intl.DateTimeFormat`（`controller.ts:243-248`）

```ts
function formatTime(value?: string | Date | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat(undefined, {          // ← 每次呼叫都新建
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
}
```

`Intl.DateTimeFormat` 的建構是標準函式庫裡最昂貴的操作之一（要解析 locale、載入 CLDR 資料）。
`format()` 本身很快，貴的是建構。

呼叫規模：`renderHistory()` 每列呼叫 1～2 次，`renderBundleDetail()` 的 event list
每筆 event 呼叫 1 次（行 1081），`workflowLifecycleDetail()` 一次呼叫 3 次（行 717-720）。
一筆有 50 個 event 的 bundle，光是重繪詳情就建構 50+ 個 formatter——**而這每 5 秒發生一次**。

**修法**（一行搬家）：

```ts
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});

function formatTime(value?: string | Date | null) {
  if (!value) return '—';
  return TIME_FORMAT.format(new Date(value));
}
```

同一個問題也出現在 `controller.ts:1634`：

```ts
form.elements.timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
```

這個只在開啟排程對話框時跑，影響小，但一樣可以提到模組層常數。

**這是投報率最高的一項改動。**

### 7.2 ❌ `escapeHtml` 每次呼叫都建立 DOM 節點（`controller.ts:233-237`）

```ts
function escapeHtml(value: unknown) {
  const node = document.createElement('span');   // ← 每次呼叫都新建元素
  node.textContent = value ?? '';
  return node.innerHTML;
}
```

全檔 **72 個呼叫點**，而且大量位於 `.map()` 內部。舉例：`renderHistory()` 的每一列
（行 762-770）至少呼叫 3 次；`renderDeploymentCard()`（行 795-806）一張卡就呼叫 10 次以上；
`componentRows()`（行 1161-1184）每個元件呼叫 14 次。

**修法 A（最小改動，重用單一節點）**：

```ts
const ESCAPE_NODE = document.createElement('span');

function escapeHtml(value: unknown) {
  ESCAPE_NODE.textContent = value ?? '';
  return ESCAPE_NODE.innerHTML;
}
```

JS 是單執行緒且這函式完全同步，重用節點沒有競態問題。

**修法 B（更快，不碰 DOM）**：

```ts
const ESCAPE_PATTERN = /[&<>"']/g;                // 提到模組層（js-hoist-regexp）
const ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
};

function escapeHtml(value: unknown) {
  return String(value ?? '').replace(ESCAPE_PATTERN, (c) => ESCAPE_MAP[c]);
}
```

⚠️ 修法 B 改變了輸出：目前的 `textContent`/`innerHTML` 版本**不會**轉義 `"` 和 `'`。
這些字串大量被塞進 HTML 屬性值（例如 `title="${escapeHtml(...)}"`，行 348），
所以 B 其實**比現況更安全**，但要跑一次 snapshot 測試確認沒有把既有輸出比對打掉。
保守起見先做 A。

### 7.3 ❌ `componentLabel` 對元件清單做線性搜尋（`controller.ts:267-273`）

```ts
function componentLabel(component: ComponentName): string {
  const registered = (state.project?.components || []).find((item) => item.key === component);
  // ...
}
```

被 `historyLabel()`（行 643）、`historyMeta()`（行 667）、`renderDeploymentCard()`（行 797）、
`scheduleBuildSummary()`（行 676）在 `.map()` 迴圈裡呼叫，
所以是 **O(列數 × 元件數)**，而且每次重繪都重跑。

**修法**：`state.project` 變動時建一次 Map。

```ts
let componentLabels = new Map<string, string>();

function rebuildComponentLabels() {
  componentLabels = new Map(
    (state.project?.components || []).map((c) => [c.key, c.display_name]),
  );
}

function componentLabel(component: ComponentName): string {
  const registered = componentLabels.get(component);
  if (registered) return registered;
  return String(component || '')
    .replace(COMPONENT_SEPARATORS, ' ')
    .replace(COMPONENT_INITIALS, (ch) => ch.toUpperCase()) || 'Component';
}
```

`rebuildComponentLabels()` 的呼叫點：`selectProject()`（行 530 之後）、
`readProjects()` 裡設定 `state.project` 的兩處（行 498）。

順帶處理 `js-hoist-regexp`：行 271-272 的 `/[-_]+/g` 和 `/\b\w/g` 兩個字面量
每次呼叫都重新編譯，提到模組層即可（上面已用 `COMPONENT_SEPARATORS` / `COMPONENT_INITIALS` 代表）。

### 7.4 ❌ 事件查詢在迴圈裡重複掃描（`controller.ts:948-954` 與 `778-785`）

```ts
for (const stage of stages) {
  const stageEvents = events.filter((event) => event.stage === stage);   // ← 每個 stage 掃一遍全部 events
  // ...三個 .some() 各再掃一次 stageEvents
}
```

`stages` 在 BUNDLE 模式下有 13 個（行 945）。events 有 N 筆的話就是 **13 × N** 次比對，
再乘上內層的三個 `some()`。`stageState()`（行 778-785）是同一個形狀，
而它又被 `renderStageTimeline()` 對每個 stage 呼叫一次。

**修法**（`js-index-maps` + `js-combine-iterations`）：先分組一次。

```ts
function groupEventsByStage(events: WorkflowEvent[]): Map<string, WorkflowEvent[]> {
  const grouped = new Map<string, WorkflowEvent[]>();
  for (const event of events) {
    const bucket = grouped.get(event.stage);
    if (bucket) bucket.push(event);
    else grouped.set(event.stage, [event]);
  }
  return grouped;
}

// workflowElementStates 內：
const byStage = groupEventsByStage(events);
for (const stage of stages) {
  const stageEvents = byStage.get(stage) ?? [];
  // 三個 some() 合併成一次 for 迴圈，遇到 failed 就 break
}
```

### 7.5 ❌ `workflowStatusGroup` 每次呼叫建立 4 個陣列（`controller.ts:607-614`）

```ts
function workflowStatusGroup(status: string): WorkflowStatusFilter {
  const normalized = String(status || 'PENDING').toUpperCase();
  if (['PENDING', 'APPROVED'].includes(normalized)) return 'pending';
  if (['RUNNING', 'DEPLOYING', 'PROMOTING', 'WAITING', 'PUBLISHING'].includes(normalized)) return 'running';
  if (['SUCCESS', 'SUCCEEDED', 'PUBLISHED'].includes(normalized)) return 'completed';
  if (['FAILED', 'PARTIAL_FAILURE', 'CANCELLED', 'REJECTED'].includes(normalized)) return 'failed';
  return 'running';
}
```

每次呼叫最多配置 4 個陣列並做線性 `includes`。
被 `filteredWorkflowHistoryItems()`（行 621）對**每一筆**記錄呼叫。

**修法**（`js-set-map-lookups`）：

```ts
const STATUS_GROUPS = new Map<string, WorkflowStatusFilter>([
  ['PENDING', 'pending'], ['APPROVED', 'pending'],
  ['RUNNING', 'running'], ['DEPLOYING', 'running'], ['PROMOTING', 'running'],
  ['WAITING', 'running'], ['PUBLISHING', 'running'],
  ['SUCCESS', 'completed'], ['SUCCEEDED', 'completed'], ['PUBLISHED', 'completed'],
  ['FAILED', 'failed'], ['PARTIAL_FAILURE', 'failed'],
  ['CANCELLED', 'failed'], ['REJECTED', 'failed'],
]);

function workflowStatusGroup(status: string): WorkflowStatusFilter {
  return STATUS_GROUPS.get(String(status || 'PENDING').toUpperCase()) ?? 'running';
}
```

同樣的模式也出現在行 780、783、950、953（`['STAGE_FAILED', 'PUBLISH_STAGE_FAILED'].includes(...)`
等等），都可以提成模組層 `Set`。

### 7.6 ⚠️ `currentHistory()` 在單一流程裡被重複呼叫（`controller.ts:626`）

呼叫點：行 757、1272、1391、1394（兩次）、2071、2078、2095、2096。

`workflows` 檢視下，每一次呼叫都會跑完整的 `workflowHistoryItems()`（行 555-605）：
建立 4 個新陣列 → 展開成第 5 個 → 用 `localeCompare` 排序。

`finishHistoryLoad()`（行 1390-1395）一個函式裡就呼叫 3 次；
`switchView()` 再呼叫 2 次。也就是說切換一次檢視，這整套至少跑 5 遍。

**修法 A**：在明顯的區域內存成區域變數。

```ts
async function finishHistoryLoad() {
  const items = currentHistory();
  if (!items.some((item) => item.id === state.selectedId)) state.selectedId = null;
  renderHistory();
  const target = state.selectedId ?? items[0]?.id;
  if (target) await selectHistory(target);
}
```

**修法 B**：加一層以 state 版本號為 key 的快取（`js-cache-function-results`），
在 `state.bundles` / `deployments` / `schedules` / `legacy` / 兩個 filter 任一變動時失效。
B 比較有效但要小心失效時機——**建議先做 A**。

順帶一提，行 602-604 的排序：

```ts
.sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))
```

`created_at` 是 ISO-8601 字串，字典序就等於時間序，
`localeCompare`（要走 Intl collation）在這裡是不必要的昂貴：

```ts
.sort((left, right) => {
  const l = left.created_at || '';
  const r = right.created_at || '';
  return l < r ? 1 : l > r ? -1 : 0;
})
```

### 7.7 ⚠️ `patchRows` 對每一列做 `outerHTML` 字串比對（`controller.ts:208`）

```ts
if (current.outerHTML !== next.outerHTML) {
```

`outerHTML` 會把整棵子樹序列化成字串。對 400 列的 history list 就是 800 次序列化。

不過外層有 `renderedHtml.get(container) === html` 這道守門（行 192），
所以只有**真的有東西變動時**才會進到這裡；而且這個 keyed reconcile 換來的是
「捲動位置與 hover 狀態不會被重設」，設計取捨是清楚的。

若要再快，可以在列上放一個 `data-signature` 屬性存內容雜湊，比對屬性而不是序列化子樹。
**優先度低**，除非實測顯示這裡真的是瓶頸。

### 7.8 ✅ 做得好的部分

- `js-tosorted-immutable`：行 1064、1156、1831 都用 `[...arr].sort()`，
  沒有原地改動傳入的陣列。行 260 的 `.slice().sort()` 同理。
- `js-flatmap-filter`：`deploymentNotes()`（行 1451）用 `flatMap` 一次完成映射與攤平。
- `js-early-exit`：`renderSource`（298）、`renderBuilds`（310）、`renderComponentCards`（333）、
  `selectProject`（529）等都在開頭就用 guard clause 早退。
- `js-cache-storage`：`localStorage` 只在對話框開啟／關閉時讀寫，沒有放在渲染路徑上。

---

## 八、Advanced Patterns（LOW，4 條）

| 規則 | 判定 |
|------|------|
| `advanced-init-once` | ❌ **違反（登出／再登入會壞）** |
| `advanced-use-latest` | ✅ 通過 |
| `advanced-event-handler-refs` | ✅ 通過 |
| `advanced-effect-event-deps` | ➖ 不適用 |

### 8.1 ❌ `initialized` 旗標 + 常駐 `setInterval` 在登出後會出事

三段程式湊在一起產生這個問題：

```ts
// controller.ts:122 — 模組載入時就抓住 DOM 節點
const elements: any = {
  target: document.querySelector('#release-target'),
  detail: document.querySelector('#release-detail'),
  // ...
};

// controller.ts:2281 — 只初始化一次
async function initialize() {
  if (initialized) return;
  initialized = true;
  // ...
  setInterval(async () => { /* 每 5 秒輪詢 */ }, 5000);   // 從不清除
}

// App.tsx:872 — 登出後整個 #app 子樹被卸載
if (!auth.authenticated || !auth.user) {
  return <LoginScreen ... />;
}
```

**登出時**：React 卸載整個 `<div id="app">`，`elements` 裡每一個節點都變成 detached。
`setInterval` 繼續每 5 秒跑 `refreshSelectedBundle()` → 用已失效的 session 打 API（大概會拿到 401），
然後把結果寫進沒人看得到的 detached 節點。

**再登入時**：React 掛上一組**全新的** DOM 節點。
`useReleaseController` 的 effect 重新跑，`import("./controller")` 命中模組快取，
`initialize()` 撞到 `if (initialized) return` 立刻返回。結果是：

- `bindEvents()` 沒有重跑 → 新的按鈕、表單、下拉選單**全都沒有 listener**
- `elements` 仍指向舊的 detached 節點 → 所有渲染都寫到看不見的地方

也就是「登出後在同一個分頁重新登入，整個介面是死的」，必須 F5 才會恢復。

**修法 A（最小、也最符合現況）**：登出時強制整頁重載。
`authSlice` 的 `logout.fulfilled` 之後導向 `window.location.assign('/')`，
讓模組狀態自然重置。粗暴但誠實，也和「這是過渡期的命令式 adapter」這個定位一致。

**修法 B（正確做法）**：讓 controller 可以被拆掉。

```ts
// controller.ts
let pollTimer: ReturnType<typeof setInterval> | undefined;
const listeners: Array<() => void> = [];   // bindEvents 時記錄每個 removeEventListener

export function teardown() {
  clearInterval(pollTimer);
  pollTimer = undefined;
  destroyBpmnViewer();
  for (const off of listeners.splice(0)) off();
  initialized = false;
  // elements 改成在 initialize() 內查詢，而非模組載入時
}
```

```ts
// App.tsx
function useReleaseController(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let teardownFn: (() => void) | undefined;
    void import("./controller").then((mod) => {
      if (!active) return;
      teardownFn = mod.teardown;
      void mod.initialize();
    });
    return () => {
      active = false;
      teardownFn?.();
    };
  }, [enabled]);
}
```

B 還要把 `const elements = {...}`（行 122-153）從模組層搬進 `initialize()`，
否則第二次初始化仍然抓到舊節點。

**另外**，即使不處理登出情境，`document.hidden` 的檢查（行 2322）只擋住分頁被隱藏的情況，
分頁在前景時輪詢永遠不停。可以考慮在記錄進入終態（`TERMINAL`）時暫停輪詢——
`refreshSelectedBundle` 內部已經有這個判斷（行 1961），只是判斷完就 return，計時器照跑。

### 8.2 ✅ 做得好的部分

- `advanced-use-latest`：`currentDetailRecord`（行 161）就是 latest-ref 模式的手寫版——
  listener 讀的永遠是最新的記錄，不是閉包捕捉當下的那份。行 1084、1211-1214 都靠這個機制。
- `advanced-event-handler-refs`：委派 handler 從 `dataset` 讀參數而非閉包捕捉，
  所以 handler 本身是穩定的。
- `bpmnRenderToken`（行 156）是正確的非同步競態防護，`renderBpmnProgress` 在兩個
  await 點之後都重新檢查（行 1024、1029）。這種細節很容易漏掉，這裡做對了。

---

## 建議施作順序

| 順序 | 項目 | 位置 | 工作量 | 收益 |
|------|------|------|--------|------|
| 1 | `formatTime` 提出 `Intl.DateTimeFormat` | `controller.ts:243` | 3 行 | 高（輪詢熱路徑） |
| 2 | `escapeHtml` 重用單一節點 | `controller.ts:233` | 3 行 | 高（72 個呼叫點） |
| 3 | 修掉 `elements.detail` listener 累加 | `controller.ts:1215` | 搬一塊 | 高（修 bug） |
| 4 | 三個健康檢查改並行 | `controller.ts:2291` | 小重構 | 高（登入後首屏） |
| 5 | `main.tsx` 移除重複的 bpmn CSS import | `main.tsx:5-6` | 刪 2 行 | 中（entry bundle） |
| 6 | `workflowStatusGroup` 改 Map | `controller.ts:607` | 10 行 | 中 |
| 7 | `componentLabel` 改 Map + regex 提模組層 | `controller.ts:267` | 15 行 | 中 |
| 8 | events 依 stage 分組 | `controller.ts:948`、`778` | 20 行 | 中 |
| 9 | `bpmn-js` 改動態 import | `controller.ts:3` | 中重構＋競態檢查 | 中高 |
| 10 | `finishHistoryLoad` 快取 `currentHistory()` | `controller.ts:1390` | 5 行 | 中低 |
| 11 | 長列表加 `content-visibility` | `styles.css` | CSS 3 行 | 中低 |
| 12 | 登出／再登入的 teardown | `controller.ts` + `App.tsx` | 較大重構 | 中（修 bug） |
| 13 | 移除未使用的 `lucide` | `package.json:24` | 1 行 | 低 |
| 14 | localStorage 加 schema 版本 | `controller.ts:31` | 小 | 低 |

1～8 項都是局部、低風險的改動，不會動到架構，可以一批做完再跑一次 `npm run quality`。
9 和 12 建議各自獨立一個 commit，因為它們涉及非同步時序。

## 對照統計

| 類別 | 總數 | 通過 | 違反 | 輕微／可改善 | 不適用 |
|------|------|------|------|--------------|--------|
| 1. Eliminating Waterfalls | 6 | 2 | 2 | 1 | 1 |
| 2. Bundle Size | 6 | 2 | 2 | 2 | 0 |
| 3. Server-Side | 10 | 0 | 0 | 0 | 10 |
| 4. Client Data Fetching | 4 | 1 | 1 | 1 | 1 |
| 5. Re-render | 15 | 3 | 0 | 1 | 11 |
| 6. Rendering | 11 | 2 | 1 | 2 | 6 |
| 7. JavaScript | 14 | 4 | 4 | 2 | 4 |
| 8. Advanced | 4 | 2 | 1 | 0 | 1 |
| **合計** | **70** | **16** | **11** | **9** | **34** |

34 條不適用主要來自「沒有 Next.js」（10 條）和「狀態不在 React 裡」（11 條）——
不是疏漏，是架構決定的。

## 驗證方式

改完之後建議按這個順序確認：

```bash
cd frontend
npm ci                    # 目前 node_modules 不存在，跑測試前要先安裝
npm run quality           # typecheck + lint + format + test + build + smoke
npm run test:e2e          # Playwright，涵蓋實際互動路徑
```

第 3 項（listener 累加）值得補一個回歸測試：
連續 `renderProjectDetail(A)` → `renderProjectDetail(B)`，
然後對 `[data-move-component]` 派發一次 click，斷言 `fetch` 只被呼叫一次。

第 9 項（bpmn 動態載入）改完務必手動確認：
快速連續點擊多筆不同的 workflow 記錄，BPMN 圖不會出現舊圖蓋新圖。

bundle 大小的實際數字要跑過 `npm run build` 才知道，本次審查沒有安裝相依套件，
所以第 2 節只描述機制、沒有給出 KB 數字。建議加一次 `rollup-plugin-visualizer` 量測後再排優先度。
