# 前端操作手冊

前端位於服務根網址，例如本機 <http://127.0.0.1:8000/>。畫面分成 release
清單、BPMN 流程圖、以及右側執行步驟。

## v2.2 BPMN 執行進度

`Releases` 沒有紀錄時，右側會先顯示 Backend-first BPMN 定義預覽。建立 Bundle 後點選
左側 release，圖形會每五秒依 workflow events 更新：

- 綠色：已完成。
- 橘色：目前執行中。
- 紅色：失敗 stage。
- 灰色虛線：因上游失敗而取消。
- 白色：尚未開始。

`Deployments` 頁點選 Frontend Only 或 Backend Only 紀錄時，會載入對應的 standalone
BPMN 與事件。圖上可拖曳，滑鼠滾輪或 `− / Fit / +` 可縮放；`Open XML` 可開啟原始
BPMN 定義。下方 stage timeline 與 append-only audit events 保留作為精確診斷資訊。

Bundle 流程圖分成三行：後端部署、前端部署與等待發布、版本準備與發布。每行由左往右閱讀，
行尾沿折返箭頭接到下一行左側；較高的圖框讓 `Fit` 顯示整張圖時，節點與文字仍容易辨識。

## 一次完整操作

1. 按 Releases 標題旁的 `+`。
2. 填入 repository、branch、commit SHA、environment；message 可留空。
3. 送出後 release 進入 `PENDING`，BPMN 停在 `Approval gate`。
4. 選取該 release，按 `Approve` 或 `Reject`，並填寫操作者名稱。
5. 若核准，狀態變成 `APPROVED`，流程停在 `Await deployment`。
6. 部署真的要開始時按 `Start deployment`，狀態變成 `DEPLOYING`。
7. 外部部署完成後，按 `Mark success` 或 `Mark failed` 記錄結果。

`Reject`、`SUCCESS` 與 `FAILED` 都是終止狀態，無法再從 UI 退回上一步。

## 哪些步驟要手動按

目前版本沒有接 Drone 或實際的部署腳本，因此以下動作都由使用者在前端按下，
或由外部程式呼叫同一組 API：

| 狀態 | 可執行動作 | 結果 |
| --- | --- | --- |
| `PENDING` | `Approve` | 進入 `APPROVED` |
| `PENDING` | `Reject` | 進入 `REJECTED` 並結束 |
| `APPROVED` | `Start deployment` | 進入 `DEPLOYING` |
| `DEPLOYING` | `Mark success` | 進入 `SUCCESS` 並結束 |
| `DEPLOYING` | `Mark failed` | 進入 `FAILED` 並結束 |

理想的正式流程通常只保留 `Approve/Reject` 給人操作；建立 release、開始部署與回報
結果由 Drone/deployment executor 自動呼叫。做法見 [CI/CD 整合](ci-integration.md)。

## 畫面判讀

- 左側 `All`：所有 release。
- `Pending`：等待核准的 `PENDING`。
- `Active`：已核准或部署中的 `APPROVED`、`DEPLOYING`。
- `Done`：`REJECTED`、`SUCCESS`、`FAILED`。
- 搜尋欄會比對 repository、branch、environment 與 commit SHA。
- BPMN 綠色代表已完成、橘色代表目前步驟、灰色代表尚未開始。
- 右側 `Process steps` 以清單顯示 completed、in progress 與 pending。

頁面在可見時每 5 秒重新取得 release 與選取中的 workflow；右上重新整理按鈕可立即
同步。縮小、放大與適合畫面按鈕只調整 BPMN 檢視，不會更動流程。

## 建立欄位

| 欄位 | 範例 | 說明 |
| --- | --- | --- |
| Repository | `smt-assistant-backend` | 專案識別名稱 |
| Branch | `pre-production` | 來源 branch |
| Commit SHA | `7c1fe1a...` | 建議填完整 Git commit SHA |
| Environment | `pre-production` | 目標環境 |
| Message | `Drone build passed` | 選填備註 |

相同 `repository + commit SHA + environment` 只能建立一次；重複建立會回傳 409。

## 重要限制

- `Start deployment` 不會執行 Podman、SSH 或任何部署命令，只更新流程紀錄。
- `Mark success` 不會自行驗證外部服務健康，請在實際 health check 成功後再按。
- `approved_by`、`rejected_by` 現由操作者自行輸入，尚未連結登入身份。
- 當前沒有復原、重開或取消 endpoint。誤按後請勿直接修改 SQLite；應新增受測試的
  管理流程，或另建一筆新的 release。

API 操作與錯誤碼詳見 [API 使用指南](api-guide.md)。
