# Drone CI/CD

本專案自身的部署定義在 [`.drone.yml`](../.drone.yml)。下游元件的 Drone pipeline
由各 repository 維護，接入方式見 [CI/CD 整合](ci-integration.md)。

## 執行流程

| 觸發 | Pipeline | 執行內容 |
| --- | --- | --- |
| 非 `main` / `deploy` 分支 push | `fast-quality` | 前端品質檢查 → Ruff 與全部 Python 測試（不收集 Python coverage） |
| `main` push / 目標為 `main` 的 pull request | `quality` | 前端品質檢查 → 全部 Python 測試與 coverage → Playwright E2E |
| `main` 的 cron / custom | `quality` | 同上，完整檢查；不部署 |
| `main` push 且 quality 成功 | `deploy` | 版本檢查 → 更新 `deploy` 分支 → Portainer redeploy → 健康檢查 → 建立 tag |
| `v*` tag | `release` | 使用 `plugins/gitea-release` 建立 Gitea Release |

四個 pipeline 都使用 Docker runner。前端先產出 `app/static`，Python 測試才可驗證首頁資源。
`deploy` 同時只執行一筆；推送 `deploy` 分支不會再次觸發品質或部署流程。
PR 以目標分支判定：目標不是 `main` 的 PR event 不執行完整檢查，但來源分支 push
仍執行 fast-quality。部署只依賴 `quality`，不會用快速檢查取代完整品質閘門。

兩種檢查都執行所有 Python 測試，包含 MCP、權限、部署、恢復與發布；目前這些測試使用
模擬上游，不能因目錄名稱而當作真實外部整合測試移除。前端 quality 仍保留原有 coverage。
完整 Python coverage 門檻維持 85%；Playwright 設定 `CI=true` 並只用一個 worker，降低
NAS 上的同時負載。

## 測試效能與診斷

Python step 掛載 Drone 的 [tmpfs temporary volume](https://docs.drone.io/pipeline/docker/syntax/volumes/temporary/)
到 `/tmp/pytest`，並以 `--basetemp=/tmp/pytest/run` 放置測試資料庫。這仍是檔案型 SQLite，
保留 WAL、foreign keys、獨立連線及並行交易測試，只讓一次性資料離開 NAS 磁碟；沒有調整
正式資料庫的 durability。tmpfs 使用主機記憶體，請在 NAS 首次執行時同步觀察記憶體與 swap。

pytest 使用 `-vv`、不緩衝的 Python 輸出與 `--durations=30`。如果單一測試超過 120 秒，
`faulthandler_timeout` 會輸出執行緒堆疊供判讀卡住位置，不會自動讓測試失敗。
慢測試排行與 `test-results/pytest.xml` 要等 pytest 正常結束才完整；被 Drone 強制終止時，
應先看 log 最後一個測試名稱與已輸出的堆疊。完整檢查另產生 `coverage.xml`。
這些檔案目前留在 pipeline workspace，尚未設定外部 artifact 上傳。

在 NAS SSH 另開終端，於測試執行期間觀察：

```sh
docker stats
```

容器尚存在時可查 Docker 的 OOM 狀態，並比對 Drone server / runner log 的取消原因：

```sh
docker inspect --format '{{.State.OOMKilled}} {{.State.ExitCode}} {{.State.Error}}' <test-container>
```

剛好執行 60 分鐘後出現 `Step is killed (canceled)` 很符合 repository timeout，
但截圖本身不能證明原因，也不能排除人工取消、自動取消或 OOM。
Drone 官方記載 [repository 預設 timeout 為 60 分鐘](https://docs.drone.io/server/user/admin/)，
需由管理員調整；這不是 `.drone.yml` 的 step 設定。
首次診斷可在 repository Settings 將 Timeout 暫調為 180 分鐘，或使用
[官方 CLI](https://docs.drone.io/cli/repo/drone-repo-update/)：

```sh
drone repo update <owner>/Release_Controller --timeout=180m
```

完成 NAS 量測後再依實測時間設定合理上限。修改本檔不會自動變更 Drone server 的 timeout。

## 手動與每晚完整檢查

在 Drone 對 `main` 建立 custom build，可手動執行完整品質檢查。每晚檢查需要另外在
repository Settings → Cron Jobs 建立排程，選 `main`；排程與時區以 Drone server 設定為準。
`.drone.yml` 接受 cron event，不會自行建立排程。cron / custom 都只跑 `quality`，
不會更新 `deploy` 分支、呼叫 Portainer 或建立 release tag。

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
