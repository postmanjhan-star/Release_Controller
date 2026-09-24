# React + TypeScript 求職導讀

這份文件不是只列語法，而是說明這個 Release Controller 為什麼這樣設計、每個函式解決什麼問題，以及面試時可以如何描述。

## 專案現在用了什麼

- React 19：用元件組合頁面，避免同一種 UI 重複撰寫。
- TypeScript 5：替 component、API 回應與表單資料建立型別契約。
- Vite：提供開發伺服器與 production bundle。
- bpmn-js：將後端 BPMN XML 畫成可縮放的執行流程圖。
- FastAPI：提供 Drone build、release、deployment、schedule 與 workflow API。

目前採漸進式遷移：`App.tsx` 已由 React 管理頁面結構和生命週期；原本穩定的部署、排程與 BPMN DOM 流程先放在 `controller.ts`。這是一種 migration boundary，能降低一次重寫造成 production regression 的風險。controller 已移除全檔 `@ts-nocheck`，會通過 TypeScript 與 ESLint；少數跨世代 payload 仍以明確的 `any` 標示邊界。新增功能應優先寫成 typed hook/component，而不是擴大這個邊界。

## React 元件與函式

### `App()`

用途：應用程式的根元件，組合 `TopBar`、`LaunchPanel`、`HistoryWorkspace` 與 dialogs。

場景：任何 React SPA 都需要一個 composition root，集中呈現最上層結構，但把細節交給子元件。

面試說法：我把單一大型 HTML 拆成具語意的元件，讓版面責任清楚，也為後續元件測試與狀態遷移建立邊界。

### `ComponentCard({ component, title })`

用途：同一套 UI 同時服務 Frontend 與 Backend，只由 props 決定內容與 DOM id。

場景：兩個畫面結構相同、資料來源不同時，使用 props 建立 reusable component，避免複製貼上。

TypeScript 效益：`component` 的型別是 `'frontend' | 'backend'`，若誤傳 `'database'`，編譯階段就會報錯。

### `HISTORY_TABS.map(...)`

用途：將資料陣列轉成一組導覽按鈕。

場景：選單、表格列、卡片清單等重複 UI。React 需要穩定的 `key` 才能正確比對前後兩次 render 的項目。

### `useReleaseController()` 與 `useEffect()`

用途：React 首次把 DOM 掛載完成後，載入既有 controller 並啟動 API 與事件流程；dependency array `[]` 表示只在 mount 時執行。

場景：串接第三方 library、訂閱事件、timer 或既有 imperative module 等「React 外部系統」。這也是 `useEffect` 最合理的用途；純資料計算通常不需要 effect。

### `DialogHeading({ eyebrow, title })`

用途：共用 dialog 標題和關閉按鈕。

場景：多個 modal 有一致視覺與無障礙規則時，抽成小元件可避免其中一個漏掉 `aria-label`。

## TypeScript 與 API 函式

### `api<T>(path, options)`

用途：統一 JSON request、HTTP error handling 與回傳型別。`T` 是泛型，例如：

```ts
const result = await api<DroneBuildList>('/api/v1/drone/frontend/builds');
result.items[0].commit_sha; // IDE 可補字，也會檢查欄位名稱
```

場景：前端有許多 REST endpoints 時，不應每個畫面都重寫 `fetch`、`response.ok` 與錯誤解析。

### `apiText(path)`

用途：取得 BPMN XML 文字。它和 `api<T>` 分開，因為 XML 不是 JSON。

場景：下載 XML、CSV、純文字或 HTML 時，回應解析方式與 JSON API 不同。

### `ApiError`

用途：除了錯誤訊息，也保存 HTTP `status`。

場景：401 可導向登入、404 顯示 not found、409 顯示資料衝突時，呼叫端可以依 status 做不同處理。

### union types

`ComponentName`、`HistoryView`、`ReleaseMode` 把允許值限制成明確集合。它們適合狀態機、頁籤、權限角色和 API enum，可消除大量拼字錯誤。

### interfaces

`DroneBuild`、`Deployment`、`ReleaseBundle`、`WorkflowEvent` 描述前後端契約。它們讓 IDE 知道欄位，也讓 refactor 更安全。不過 TypeScript 型別只存在編譯期；若資料來自不可信外部服務，production 專案還應加 Zod 等 runtime validation。

## Controller 中值得理解的函式

| 函式 | 效用 | 常見使用場景 |
| --- | --- | --- |
| `Promise.allSettled(...)` | 平行載入多組歷史，單一 API 失敗不會抹掉其他成功結果 | Dashboard 同時讀多個服務 |
| `loadViewHistory(view)` | 只抓目前頁籤需要的資料 | Tab 切換、lazy loading |
| `selectedBuild(component)` | 從狀態推導目前選取的 build | Derived state，避免儲存重複物件 |
| `visibleBuilds(component)` | 依 branch 過濾 builds | 搜尋、filter、selector |
| `selectFirstPromotable(component)` | 用 `find` 選第一個可部署 build | 預設選項、fallback |
| `renderBpmnProgress(...)` | 非同步載入 XML，交給 bpmn-js，並疊加 workflow marker | React 串接 imperative visualization library |
| `workflowElementStates(...)` | 將 events/current stage 轉成 completed/current/failed/cancelled | 狀態機投影成 UI 狀態 |
| `localDateTimeValue(date)` | 將 `Date` 轉為 `datetime-local` 可接受格式 | 排程表單與時區處理 |
| `setInterval(...)` + `document.hidden` | 每 5 秒更新執行中項目，背景分頁跳過 request | Polling、降低不必要流量 |
| `escapeHtml(value)` | controller 插入 HTML 字串前進行編碼 | 防止 XSS；React JSX 一般會自動 escape |

## 面試可以展示的工程判斷

1. 我沒有一次重寫高風險的 release controller，而是先建立 React composition root 與 TypeScript API/domain boundary，保持既有功能可回歸測試。
2. 我用 reusable component 消除 Frontend/Backend 卡片重複，並用 union type 限制合法 component。
3. 我把 JSON 與 text response 分成 `api<T>`、`apiText`，集中處理 HTTP error。
4. 我保留 contract test、typecheck、production build 與 smoke test 四層驗證。
5. 我會把下一階段拆成 `useBuilds`、`useHistory`、`BpmnViewer`，逐步移除 legacy adapter 的明確 `any`，而不是讓 migration boundary 永久存在。

## 練習順序

1. 在 `ComponentCard` 新增 `isLoading` prop，觀察 TypeScript 如何要求兩個呼叫端都補值。
2. 把 toast 改成 React `useState`，練習 state 與 conditional className。
3. 把 build API 搬成 `useBuilds(component)` hook，練習 loading/error/data 三態。
4. 用 React Testing Library 測試 branch 切換和 disabled button。
5. 最後把 BPMN viewer 包成 `BpmnViewer`，在 effect cleanup 呼叫 `viewer.destroy()`。

每次修改可依序執行：

```powershell
Set-Location frontend
npm run typecheck
npm run quality
npm run test:e2e
```
