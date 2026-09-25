# Gitea / Drone 整合

本頁說明 Release Controller 如何部署下游專案。本專案自身的 Portainer 部署另見 [CI/CD](cicd.md)。

## 現行流程

```text
Gitea repository → Drone build
                        ↓
操作者登入 Controller → 選專案、元件、build 與 target
                        ↓
Controller 呼叫 Drone promote → 追蹤 promotion build
                        ↓
部署成功 → 操作者提出發布請求 → Gitea tag / Release
```

- **Gitea** 保存程式碼、提供 OAuth 登入及 Release API。
- **Drone** 負責下游 build、實際部署與該 pipeline 的健康檢查。
- **Controller** 驗證 build、安排順序、呼叫 promotion、追蹤結果與保存事件。

Bundle 依專案元件順序執行，可選任意子集；並非固定 Backend → Frontend。
Controller 不直接登入下游主機，也不執行下游部署 shell。

## 接入一個專案

1. 在 Drone 啟用 repository，讓來源 build 能正常完成。
2. 在下游 repository 設定可接受 promotion event 與預定 target 的部署 pipeline。
3. 在 Controller 的 **Projects → Connections** 建立 Drone / Gitea 連線並測試。
4. 建立專案，設定元件的 Drone / Gitea repository、順序與預設 target。
5. 執行專案驗證，以測試環境的成功 build 驗證單元件部署，再驗證 Bundle。

連線解析順序為元件覆寫 → 專案設定 → 同類型預設連線。
Token 需具有對應 repository 的查詢、promotion 或 Release 建立權限。
Branch 來自 Drone repository metadata 與近期 build history；target 是部署環境，兩者不同。

## 追蹤與失敗

Source build 與 promotion build 分開保存。Controller 依 promotion build 狀態判定部署結果，
下游 pipeline 應將部署及健康檢查失敗回報為 build failure。
啟用背景 worker 後會持續追蹤未完成工作；也可透過 UI / API refresh。

Bundle 某元件失敗會停止後續元件；已成功的元件保留結果。
發布另有獨立狀態，Gitea Release 失敗不會抹掉部署成功紀錄。
呼叫 promotion 或建立 Release 遇到不確定結果時，先查詢歷程及上游狀態，不盲目重送。

## 舊版核准流程與尚未支援項目

舊版 `POST /api/v1/releases` 仍提供 `PENDING → APPROVED → DEPLOYING → SUCCESS/FAILED`
及 `REJECTED` 流程。其 start / finish API 只記錄狀態，沒有接上 Drone promotion。
目前的直接 promotion 與排程也沒有自動串接這個人工核准關卡；不要假設所有部署都必經審核。

Controller 的業務 API 已受 Gitea session 保護，尚無 service token 或 Gitea webhook
接收／驗章功能。不能把未帶 session 的 CI curl 範例當作可用的正式整合方式。
API 契約見 [API 使用指南](api-guide.md)，權限界線見[安全指南](security.md)。
