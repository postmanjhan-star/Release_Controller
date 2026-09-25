# 更新與維運

部署由 Drone 更新 `deploy` 分支，再透過 Portainer 重建服務。
初次設定見[部署設定](deployment.md)，pipeline 與 secrets 見 [CI/CD](cicd.md)。
以下 Docker 指令需在實際容器主機執行；亦可從 Portainer 查看狀態及 log。

## 更新版本

1. 完成[品質檢查](quality-gates.md)，更新 `pyproject.toml` 版本與 `uv.lock`。
2. 部署前備份 SQLite 及安全保存的 runtime 設定、`APP_SECRET_KEY`。
3. 提交並合併至 `main`，觀察 quality、deploy 與 tag 觸發的 release pipeline。
4. 確認 `/health`、登入、既有專案與歷程，以及預期的版本已上線。

目前 pipeline 不會自動備份或自動回滾。容器啟動會先執行 migration；不要只替換前端資源，
也不要在部署成功前手動建立發版 tag。

```bash
docker ps --filter name=release-controller
docker logs --since 10m release-controller
curl -fsS http://127.0.0.1:3100/health
```

URL 請換成實際綁定位址。`/health` 只驗證應用程式與資料庫，不代表上游服務或所有業務操作正常。

## 背景工作與通知

設定 `BACKGROUND_WORKER_ENABLED=true` 後，worker 處理到期排程、未完成部署的追蹤與復原、
通知 outbox。SQLite 架構維持單一服務 instance / worker。

| 設定 | 用途 |
| --- | --- |
| `SMTP_HOST`、`SMTP_PORT`、`SMTP_FROM_ADDRESS` | 郵件伺服器與寄件者；host 留空時不寄送 |
| `SMTP_USERNAME`、`SMTP_PASSWORD` | SMTP 驗證，依伺服器需求設定 |
| `SMTP_STARTTLS` / `SMTP_SSL` | 使用 implicit TLS 時設 `SMTP_SSL=true`、`SMTP_STARTTLS=false` |
| `SMTP_TLS_VERIFY` | 正式環境設為 `true`；範例檔的開發值需調整 |
| `SMTP_TLS_CA_FILE` | 使用內部 CA 時，指定已掛載至容器的 PEM 檔案 |
| `SMTP_DEFAULT_RECIPIENTS` | Workflow 通知沒有專屬收件人時的 fallback |
| `SMTP_MANAGED_RECIPIENT_TARGETS` | 使用共用名單的排程目標，預設 `production` |
| `NOTIFICATION_ATTACHMENT_MAX_BYTES` | 附件上限，預設 10 MiB |

共用名單在 **Email recipients** 管理，並與適用排程的額外收件人合併。
排程建立及流程終態會依設定產生通知；寄送失敗不回滾部署。
`GET /api/v1/notifications/outbox` 可查看 attempts、next_attempt_at 與 last_error，需登入。
重試受 `SMTP_MAX_ATTEMPTS`、`SMTP_RETRY_SECONDS` 與 claim timeout 控制。

## SQLite 備份

運行中的 SQLite 可能有 WAL，請用 backup API 取得一致快照，不直接複製主檔：

```bash
BACKUP_NAME="release-$(date +%Y%m%d-%H%M%S).db"
docker exec --env BACKUP_PATH="/data/$BACKUP_NAME" release-controller python -c \
  'import os, sqlite3; src=sqlite3.connect("/data/release.db"); dst=sqlite3.connect(os.environ["BACKUP_PATH"]); src.backup(dst); dst.close(); src.close()'
mkdir -p backups
docker cp "release-controller:/data/$BACKUP_NAME" "backups/$BACKUP_NAME"
```

備份包含歷程、登入 session 與通知附件。將副本保存到另一個儲存位置，限制存取，並定期在
隔離環境驗證還原。`APP_SECRET_KEY` 需另外安全備份；沒有原 key 就無法解開既有連線 token。

## 還原與回滾

1. 確認部署版本、備份時間及 Portainer Stack 實際掛載的 `DATA_DIR`。
2. 保留當前一致性備份，停止服務，避免背景 worker 在還原期間執行。
3. 使用經驗證的資料庫備份取代 `/data/release.db`；只在服務已停止且舊資料已保存後，
   移除該資料庫殘留的 `release.db-wal` 與 `release.db-shm`，避免混用不同快照。
4. 確認檔案 owner / 權限與原掛載一致，以相容版本啟動，再查 `/health`、登入與歷程。
5. 恢復背景工作前，確認排程、未完成部署及 outbox，避免舊快照重送已完成的工作。

應用程式回滾優先採用 revert commit、遞增版本後重新走 CI。
若需緊急改用舊版 image / Git ref，由維運人員在 Portainer 操作並確認 schema 相容性；
換回舊程式不會自動還原資料庫。不要覆寫既有版本 tag 或任意執行 Alembic downgrade。

Portainer 呼叫後失敗時，`deploy` 分支可能已更新，但服務未必成功替換。
先確認實際容器版本與 log，再修復或回滾；不能只看 Git ref 判斷上線結果。
