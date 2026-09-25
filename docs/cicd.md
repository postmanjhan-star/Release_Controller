# Drone CI/CD

本專案自身的部署定義在 [`.drone.yml`](../.drone.yml)。下游元件的 Drone pipeline
由各 repository 維護，接入方式見 [CI/CD 整合](ci-integration.md)。

## 執行流程

| 觸發 | Pipeline | 執行內容 |
| --- | --- | --- |
| push / pull request（排除 `deploy` 分支） | `quality` | 前端品質檢查 → Python 品質檢查 → Playwright E2E |
| `main` push 且 quality 成功 | `deploy` | 版本檢查 → 更新 `deploy` 分支 → Portainer redeploy → 健康檢查 → 建立 tag |
| `v*` tag | `release` | 使用 `plugins/gitea-release` 建立 Gitea Release |

三個 pipeline 都使用 Docker runner。前端先產出 `app/static`，Python 測試才可驗證首頁資源。
`deploy` 同時只執行一筆；推送 `deploy` 分支不會再次觸發品質或部署流程。

## 初次設定

1. 在 Drone 同步並啟用 repository，確認 Gitea webhook 與 Docker runner 正常。
2. 依[部署設定](deployment.md)建立 Portainer Git Stack，追蹤 `deploy` 分支。
3. 在 Drone repository 設定下列 secrets。
4. 確認 `.drone.yml` 的 `verify-deployment` URL 能從 runner 連到實際服務。
5. 以 feature branch / pull request 驗證 quality；合併 `main` 後觀察部署及 tag pipeline。

| Drone secret | 用途 |
| --- | --- |
| `gitea_deploy_user` | Git fetch / push 使用者 |
| `gitea_deploy_token` | 可讀 repository、推送 `deploy` 分支及建立 `v*` tag 的 token |
| `portainer_url` | Portainer 服務 URL |
| `portainer_api_token` | 可重新部署指定 Stack 的 API key |
| `portainer_stack_id` | 目標 Git Stack ID |
| `portainer_endpoint_id` | 該 Stack 所屬環境 ID |
| `gitea_server` | 發布 Release 使用的 Gitea URL |
| `gitea_release_token` | 可在此 repository 建立 Release 的 token |

Runtime 的 OAuth、SMTP、`APP_SECRET_KEY` 等保存在 Portainer 的 Stack 設定中。
Drone 不會讀取開發者的 `.env`。Gitea 的 branch / tag protection 需允許部署帳號執行上述寫入。

## 版本管理

唯一版本來源是 `pyproject.toml` 的 `[project].version`。在 repository 根目錄更新：

```sh
uv version --bump patch --no-sync
uv version --short
git diff -- pyproject.toml uv.lock
```

也可使用 `--bump minor`、`--bump major`，或指定版本如 `uv version 3.1.0 --no-sync`。
將版本與 `uv.lock` 一起提交；版本字串不加 `v`，pipeline 會建立 `v<version>` tag。

`release-preflight` 在更新部署分支前檢查 tag：同名 tag 若指向其他 commit 會停止。
健康檢查通過後才推送新 tag；若 tag 已指向同一 commit，則不重建。
手動推送 `v*` tag 也會觸發 release pipeline，因此日常發版應交由上述部署流程建立 tag。

## 失敗處理

- **quality 失敗**：先修復失敗的檢查，部署不會執行。
- **版本衝突**：遞增版本並提交，不覆寫已發布 tag。
- **推送分支失敗**：檢查 Git 憑證與保護規則；既有 `deploy` 分支需可 fast-forward。
- **Portainer redeploy 失敗**：檢查 Stack / environment ID、API key、Git 存取與建置 log。
- **健康檢查失敗**：檢查容器 log、綁定位址與 migration。流程最多嘗試 45 次，每次 timeout 5 秒，失敗間隔 2 秒。
- **Release 未建立**：檢查 tag pipeline 的 token、服務 URL 與執行結果。

目前 Portainer 呼叫使用 `curl -k`，會略過該請求的 TLS 憑證驗證；這是現有設定，非已完成
憑證驗證。部署失敗也不會自動還原分支、容器或資料庫；處理方式見[更新與維運](operations.md)。
