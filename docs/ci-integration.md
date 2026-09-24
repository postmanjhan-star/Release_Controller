# Gitea 與 Drone 整合指南

本 repository 根目錄的 `.drone.yml` 已負責 Release Controller 自身的品質檢查與 container
部署。本章則是下游 Frontend/Backend 發布流程的下一階段整合設計；**目前仍未包含 Gitea
webhook endpoint 或 API authentication**，下列通報流程需由 CI/CD 維護者另行建立後才會
自動運作。

## 建議責任分工

```text
Gitea push
  -> Drone test/build
  -> POST /releases                         建立 PENDING
  -> 人員在 Release Controller approve
  -> deployment executor poll/receive event
  -> POST /deployment/start                 記錄 DEPLOYING
  -> executor 實際部署 + health check
  -> POST /deployment/finish                記錄 SUCCESS/FAILED
```

- **Gitea**：保存 source、commit 與 webhook event。
- **Drone**：test/build，成功後建立 release，不替人核准正式環境。
- **Release Controller**：審核狀態、BPMN 與歷程的唯一來源。
- **Deployment executor**：操作目標平台並回報結果。

至少在第一階段，保留 `Approve/Reject` 為人工動作；其餘三個 API 動作適合自動化。

## Drone 建立 release

概念性 shell step：

```bash
set -eu

BASE_URL="http://release-controller:8000/api/v1"
PAYLOAD="$(jq -n \
  --arg repository "$DRONE_REPO" \
  --arg branch "$DRONE_BRANCH" \
  --arg commit_sha "$DRONE_COMMIT_SHA" \
  --arg environment "pre-production" \
  --arg message "Drone build $DRONE_BUILD_NUMBER passed" \
  '{repository:$repository, branch:$branch, commit_sha:$commit_sha,
    environment:$environment, message:$message}')"

RESPONSE="$(curl -fsS -X POST "$BASE_URL/releases" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD")"
RELEASE_ID="$(printf '%s' "$RESPONSE" | jq -r .id)"
printf 'Created release %s\n' "$RELEASE_ID"
```

`DRONE_*` 變數名稱需依實際 Drone 版本與 pipeline 調整。`jq` 必須存在於 step image。
正式串接 authentication 後，token 應由 Drone secret 注入 header，不能寫進 YAML 或 log。

相同 repository、commit、environment 重試時會回 409。Pipeline 應採以下其中一種策略：

- 將 409 視為已建立，再以精確 filter 查詢既有 release。
- 第一次回應後把 `release_id` 保存成 deployment metadata。
- 未來新增 idempotency key；在 endpoint 實作前不能假設已支援。

## 等候核准

現有 API 沒有 callback/event stream。Deployment executor 可定期查詢單筆 release：

```bash
curl -fsS "$BASE_URL/releases/$RELEASE_ID"
```

- `PENDING`：繼續等候，但必須有 timeout。
- `APPROVED`：進入部署。
- `REJECTED`：停止 pipeline，視為有意識的拒絕而非系統錯誤。
- 其他狀態：依 recovery policy 處理，不要重複 start/finish。

建議 polling 間隔 5 至 15 秒並有最大等待時間。較成熟的版本可加入 signed callback 或
message queue，避免長時間佔用 Drone runner。

## 部署與回報

Executor 取得 `APPROVED` 後：

```bash
curl -fsS -X POST "$BASE_URL/releases/$RELEASE_ID/deployment/start"

if ./deploy-and-health-check.sh; then
  curl -fsS -X POST "$BASE_URL/releases/$RELEASE_ID/deployment/finish" \
    -H 'Content-Type: application/json' \
    -d '{"status":"SUCCESS","message":"Deployment and health check passed"}'
else
  curl -fsS -X POST "$BASE_URL/releases/$RELEASE_ID/deployment/finish" \
    -H 'Content-Type: application/json' \
    -d '{"status":"FAILED","message":"Deployment or health check failed"}'
  exit 1
fi
```

實作時應用 `trap` 或等效的 finally 機制，避免 executor 在 `deployment/start` 後異常退出，
讓 release 永遠卡在 `DEPLOYING`。錯誤 message 不得包含 token、密碼或完整敏感 log。

## Gitea webhook

原規格規劃未來加入 `POST /api/v1/webhooks/gitea`。若實作：

- 驗證 Gitea 提供的 HMAC signature，使用原始 request body 計算並 constant-time compare。
- 檢查 event type、repository allowlist、branch/environment mapping。
- 以 delivery ID 或 commit/environment 做去重。
- Secret 由環境/secret manager 注入，不能寫在 repository。
- Webhook 只建立 release；不應繞過 approval gate 直接部署。

若 Drone 已經在 build 成功後建立 release，Gitea webhook 就不應重複建立同一筆。先選定
單一建立來源，或者明確做 idempotency。

## 網路

在同一 Podman `release-net` 的 CI/executor container 可使用
`http://release-controller:8000`。若 Drone runner 不在同一 network，建議透過 TLS reverse
proxy 暴露受驗證 endpoint；目前 `127.0.0.1:3100` 只供 VM 本機與 SSH tunnel 使用。

正式啟用前請完成[安全指南](security.md)的 authentication、authorization、secret 與
audit 檢核。
