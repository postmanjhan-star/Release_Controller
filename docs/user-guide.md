# 操作手冊

從服務首頁登入 Gitea。導覽提供 **Releases**、**Workflows**、**Email recipients** 與
**Projects**；Connections 位於 Projects 的設定切換區。

## 首次設定

1. 到 **Projects → Connections** 新增 Drone 與 Gitea 連線，填入 URL、token 並測試。
2. 建立專案，設定名稱、預設 target 與連線。
3. 新增元件，指定各自的 Drone / Gitea repository、是否啟用與是否需要發布。
4. 調整元件部署順序並執行專案驗證。

連線可由元件覆寫專案設定，未指定時使用同類型預設連線。
若寫入時收到 403，請確認是否啟用 `PROJECT_ADMIN_WRITES` 及帳號的 Gitea 管理員身分。

## 選擇版本與部署

1. 在 Releases 的 **New deployment** 區選擇 Project 與 Target。
2. 為要部署的元件各自選擇 branch 與可部署的 source build。
3. 選擇單元件部署，或按 **Release selected components** 依專案順序執行。
4. 確認目標、build 與選用的通知附件後送出。
5. 從歷程查看各元件結果、source / promotion build、stage 與事件。

Branch 是來源分支；Target 是 Drone promotion 的部署目標，不必同名。
Bundle 會循序執行；某元件失敗後停止後續元件，已成功部分保留結果。
若需要重試未成功元件，可使用 [Bundle retry API](api-guide.md)。

## 發布 Gitea Release

部署成功不代表已發布版本。符合發布條件時，開啟 **Publish**，填入語意化版本、名稱與
release notes，再送出。未啟用發布的元件會略過。

**Deployment** 與 **Publish** 分別顯示結果。發布失敗時先查看錯誤碼及 Gitea tag，
確認是否為相同版本指向不同 commit，再決定重試或更換版本。

## 排程與通知

使用 **Schedule** 設定日期、時間、IANA 時區與收件人；production 排程需填版本與更新內容。
可附加通知檔案，預設上限 10 MiB。排程只安排部署；Gitea Release 仍需另行發布。

- 背景 worker 啟用後，排程到期才會自動執行。
- 尚未執行的排程可取消；已開始的工作不可當作待執行排程取消。
- **Email recipients** 維護共用通知名單；寄送條件與 SMTP 設定見[維運文件](operations.md)。

## 歷程判讀

Workflows 可依類型與狀態篩選部署、Bundle、排程及舊版核准紀錄。
BPMN、stage timeline 與事件歷程用於查看執行過程；實際參與元件、順序及結果以紀錄明細為準。

| 顯示 | 意義 |
| --- | --- |
| `WAITING` / `PENDING` | 等待執行 |
| `PROMOTING` / `DEPLOYING` | 已開始提交或部署 |
| `SUCCESS` | 部署成功，仍需另看 Publish 狀態 |
| `FAILED` / `PARTIAL_FAILURE` | 全部或部分失敗，查看錯誤與 stage |
| `CANCELLED` | 該元件或排程已取消，查看原因 |

BPMN 以綠色表示完成、橘色表示目前階段、紅色表示失敗，灰色虛線表示取消。
頁面可見時會定期更新，也可使用 Refresh；縮放流程圖不影響執行狀態。

## 舊版人工核准

歷史核准紀錄仍支援 `Approve / Reject`、`Start deployment` 與 `Mark success / failed`。
登入啟用時，核准者取自登入身分；start / finish 僅記錄外部部署狀態。
這是獨立的舊版流程，目前的直接 promotion 與排程不會自動經過這個核准關卡。
