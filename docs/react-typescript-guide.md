# React + TypeScript 導讀

前端由 React + TypeScript、Redux Toolkit、Vite 與 bpmn-js 組成。
React 負責頁面結構及登入生命週期，許多互動仍由 `controller.ts` 操作 DOM；這是目前的實作邊界。

## 閱讀順序

| 檔案 | 重點 |
| --- | --- |
| `frontend/src/main.tsx` | React 與 Redux 入口 |
| `frontend/src/App.tsx` | 登入畫面、導覽、部署區、歷程與 dialogs |
| `frontend/src/authSlice.ts`、`store.ts` | Session 載入、登出與 typed Redux hooks |
| `frontend/src/domain.ts` | Project、Component、Deployment、Schedule 等前端型別 |
| `frontend/src/api.ts` | JSON / text request 與 `ApiError` |
| `frontend/src/controller.ts` | 專案選取、build、表單、歷程、輪詢與事件綁定 |
| `frontend/src/bpmnViewer.ts` | 按需載入 BPMN viewer |

## 元件與生命週期

`App` 依登入狀態顯示操作畫面。`useReleaseController(enabled)` 在已登入時動態載入
controller 並初始化；effect cleanup 呼叫 `teardown()`，停止輪詢並清理資源。
登入後重新掛載時會重新查找 DOM，避免沿用已卸載的節點。

`LaunchPanel` 提供容器，元件卡片由 controller 依所選專案產生。
`ComponentName` 現在是 `string`，不再限制為 frontend / backend；元件是否合法由專案資料與後端驗證。
`HistoryView`、`ReleaseMode` 等封閉集合仍適合用 union types 表達。

## API 與型別

`api<T>` 統一處理 JSON、HTTP 錯誤與回傳型別；FormData 不會被強制設為 JSON content type。
`apiText` 讀取 BPMN XML。`ApiError.status` 可用來區分 401、404、409 等錯誤。

```ts
import { api } from './api';
import type { DroneBuildList } from './domain';

const builds = await api<DroneBuildList>(
  '/api/v1/projects/demo/components/api/builds',
);
const commit = builds.items[0]?.commit_sha;
```

範例的專案與元件需存在。泛型讓編譯器檢查使用方式，但不會在執行期驗證伺服器回應的結構。

## 修改時留意

- React 與 controller 共用的 DOM id 是介面契約，改名時需同步修改並驗證。
- 非同步載入與輪詢需處理卸載，避免登出後繼續請求或重複綁定事件。
- bpmn-js 動態載入，相關樣式隨 viewer chunk 載入。
- Controller 組 HTML 時需編碼外部文字；React JSX 則使用自身的 escaping。
- 新功能逐步抽成型別清楚的元件或 hook，避免擴大手動 DOM 管理範圍。

修改後於 `frontend` 執行 `npm run quality`；影響使用者操作時另跑 `npm run test:e2e`。
測試範圍與 coverage 門檻見[品質檢查](quality-gates.md)。
