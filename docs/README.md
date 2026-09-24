# Release Controller 文件中心

本目錄記錄 Release Controller v3.0 實際可用的功能，以及接入 Gitea/Drone 的建議方式。
「已實作」與「規劃中」會明確標示，避免把設計建議當成現有功能。

## 建議閱讀順序

1. 第一次在本機開發：閱讀[本機安裝與執行](getting-started.md)。
2. 已開啟前端：閱讀[前端操作手冊](user-guide.md)。
3. 要從 CI 或指令操作：閱讀 [API 使用指南](api-guide.md)。
4. 要部署到 Gitea VM：閱讀 [Podman 部署](podman-deployment.md)。
5. 要讓 `main` push 自動部署：閱讀[自動 CI/CD](cicd.md)。
6. 要更新正式環境：接著閱讀[更新與維運](operations.md)。
7. 要接 Drone/Gitea：閱讀 [CI/CD 整合](ci-integration.md)及[安全指南](security.md)。
8. 要了解多專案與 1～N 元件的設計：閱讀
   [v3.0 多專案規格](specifications/MULTI_PROJECT_V3_0_SPEC.md)。

## 文件一覽

| 文件 | 主要讀者 | 說明 |
| --- | --- | --- |
| [本機安裝與執行](getting-started.md) | 開發者 | 安裝依賴、migration、啟動、測試 |
| [前端操作手冊](user-guide.md) | 審核者、操作人員 | 狀態、按鈕、BPMN 顏色與操作流程 |
| [API 使用指南](api-guide.md) | 開發者、CI 維護者 | Endpoint、JSON、錯誤碼與 curl 範例 |
| [系統架構](architecture.md) | 開發者、維運人員 | 執行元件、資料流、資料表與設定 |
| [Podman 部署](podman-deployment.md) | 維運人員 | Gitea VM 首次部署與手動替代流程 |
| [自動 CI/CD](cicd.md) | CI/CD 維護者 | main push、Drone runners 與 Podman 自動部署 |
| [更新與維運](operations.md) | 維運人員 | 更新、log、備份、還原、回滾 |
| [CI/CD 整合](ci-integration.md) | CI/CD 維護者 | Drone 自動通報與部署執行者的介面 |
| [測試圍欄與品質閘門](quality-gates.md) | 開發者、Reviewer | 本機與 CI 的 lint、coverage、build、E2E 門檻 |
| [安全指南](security.md) | 架構師、維運人員 | 網路邊界、服務 token、OAuth/OIDC |
| [疑難排解](troubleshooting.md) | 所有人 | 常見問題與診斷指令 |
| [v3.0 多專案規格](specifications/MULTI_PROJECT_V3_0_SPEC.md) | 開發者 | 多專案、連線登錄表、1～N 元件與遷移設計 |
| [原始規格](specifications/RELEASE_CONTROLLER_SPEC.md) | 開發者 | 專案需求、範圍與驗收條件 |
| [v2.2 規格](specifications/RELEASE_WORKER_V2_2_SPEC.md) | 開發者 | Release Worker、Drone 與 backend-first workflow |

## 目前功能邊界

已實作：Gitea OAuth2 + PKCE 登入、可信 actor audit、多專案與加密連線登錄表、每個專案
1～N 個自訂元件、Drone build 查詢與循序 promotion、Gitea Release 發布、持久化排程、
SMTP outbox 與附件、動態 BPMN 執行狀態、SQLite migration、測試、container build，以及
`main` push 通過品質閘門後執行 repository 部署腳本的 Drone pipeline。

專案與連線寫入可透過 `PROJECT_ADMIN_WRITES=true` 限制為 Gitea administrator；release、
核准與發布等操作尚未細分角色，也尚未提供 service token 或 Gitea webhook 驗章。下游元件
Drone pipeline 仍由各自 repository 管理，Controller 的部署動作是呼叫既有 Drone
promotion API，不直接操作下游主機或容器。
