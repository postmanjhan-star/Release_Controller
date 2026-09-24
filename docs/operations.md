# 更新與維運

## 排程與 SMTP worker

正式環境先執行 `alembic upgrade head`，再設定 `BACKGROUND_WORKER_ENABLED=true`。
SQLite 部署只啟動一個 Uvicorn worker；排程和 SMTP dispatcher 與 API 共用同一資料庫。
SMTP 至少需設定 `SMTP_HOST` 與 `SMTP_FROM_ADDRESS`。通知名單可由 UI 的
`Email recipients` 或 `/api/v1/notifications/recipients` 管理；目標為
`SMTP_MANAGED_RECIPIENT_TARGETS`（預設 `production`）的排程，會通知名單中每個地址，
並合併個別排程填寫的額外收件人。建立正式站排程時會先寄出排程通知，部署的完成或失敗
workflow event 會再寄出結果通知。`SMTP_DEFAULT_RECIPIENTS` 則是其他 workflow event
沒有專屬收件人時的 fallback。587/STARTTLS 是預設值；implicit TLS 請設 `SMTP_SSL=true` 並關閉
`SMTP_STARTTLS`。密碼只放 server env file。
TLS 憑證預設會驗證；內部 CA 可用 `SMTP_TLS_CA_FILE` 指向 container 內的 PEM bundle。
`SMTP_TLS_VERIFY=false` 只能作為短期診斷手段，不應成為正式設定。

寄送失敗可由 `GET /api/v1/notifications/outbox` 查看 `attempts`、`next_attempt_at` 與
`last_error`。worker 會依 `SMTP_RETRY_SECONDS` 指數退避，最多嘗試
`SMTP_MAX_ATTEMPTS` 次；不會因 SMTP 不可用而回滾部署 workflow。
升級 migration 會把當下位置寫入 notification cursor，因此只寄送升級後的新事件。

## 建議發版流程

1. 本機完成程式修改、前端 build 與測試。
2. commit 並 push 到 Gitea；正式版本建議建立 immutable Git tag。
3. VM 在固定 source 目錄執行 `git pull --ff-only`。
4. 部署前備份 SQLite。
5. 使用既有部署腳本 build、替換 container、執行 health check。
6. 確認 UI、API、log 與資料仍正常。

## 本機準備更新

```powershell
Set-Location frontend
npm run build
Set-Location ..

uv run test
git status --short
git diff --check
git add <本次變更檔案>
git commit -m "feat: describe the change"
git push origin main
```

請只提交本次變更，不要把 `.env`、database 或 VM secret 放進 Git。正式發版可加 tag：

```bash
git tag -a v1.1.0 -m "Release v1.1.0"
git push origin v1.1.0
```

## VM 更新

先確認沒有 VM 上未提交的修改：

```bash
cd "$HOME/services/release-controller-src"
git status --short
git fetch --tags origin
git checkout main
git pull --ff-only origin main
git log -1 --oneline
```

若 `git status` 不乾淨，先釐清檔案來源，不要用 `git reset --hard` 隨意覆蓋。

備份完成後執行：

```bash
bash ./scripts/deploy-release-controller.sh "$PWD"
curl -fsS http://127.0.0.1:3100/health
podman logs --tail 100 release-controller
```

若 VM 沒有部署腳本，依 [Podman 部署](podman-deployment.md)的手動流程操作，並替每次
build 使用版本 tag，而不只覆寫 `latest`：

```bash
VERSION="$(git describe --tags --always --dirty)"
podman build -f Containerfile -t "localhost/release-controller:$VERSION" .
podman tag "localhost/release-controller:$VERSION" localhost/release-controller:latest
```

## 健康與診斷

```bash
curl -fsS http://127.0.0.1:3100/health
podman ps --filter name=release-controller
podman logs --since 10m release-controller
podman stats --no-stream release-controller
podman inspect release-controller
podman system df
```

正常 `/health` 回 HTTP 200 且 database=`ok`。HTTP 503 表示 process 可回應，但 SQLite
不可用；connection refused 則代表 container 未啟動、port 未 publish 或 process 已退出。

## SQLite 備份

建議每次部署前備份，並定期把備份複製到 VM 之外。服務運行時使用 SQLite backup
API，可取得一致性 snapshot：

```bash
BACKUP="/data/release-$(date +%Y%m%d-%H%M%S).db"
podman exec --env BACKUP="$BACKUP" release-controller python -c \
  "import os, sqlite3; src=sqlite3.connect('/data/release.db'); dst=sqlite3.connect(os.environ['BACKUP']); src.backup(dst); dst.close(); src.close()"

ls -lh "$HOME/services/release-controller/data"/release-*.db
```

備份 retention 例：至少保留最近 7 份與每週 4 份，實際依公司 RPO/RTO 制定。定期在
隔離環境做還原演練，只有產生檔案但未驗證的備份不算可用備份。

## 還原資料庫

還原會中斷服務。先確認備份檔與目標路徑，再執行：

```bash
DATA="$HOME/services/release-controller/data"
BACKUP="$DATA/release-YYYYMMDD-HHMMSS.db"

test -f "$BACKUP"
podman stop release-controller
cp "$DATA/release.db" "$DATA/release-before-restore-$(date +%Y%m%d-%H%M%S).db"
cp "$BACKUP" "$DATA/release.db"
rm -f "$DATA/release.db-wal" "$DATA/release.db-shm"
podman start release-controller
curl -fsS http://127.0.0.1:3100/health
```

`rm` 只可針對上面已確認的固定 data 目錄與兩個 sidecar 檔。若不確定路徑，停止並請
維運人員檢查，不要以 wildcard 刪除。

## Application rollback

Application rollback 與 database rollback 是兩件事：

1. 找出上一個已驗證 image：`podman images localhost/release-controller`。
2. 停止並移除目前 container，但保留 `/data`。
3. 以完全相同的 network、port、volume、env 啟動上一版 image。
4. 執行 `/health` 與 UI smoke check。

若新版已執行無法向後相容的 migration，舊 image 可能不能讀取新 schema。此時應使用
預先測試的 downgrade，或把「部署前資料庫備份」與舊 image 一起還原。任何 destructive
migration 都應在維護窗口執行，不能假設換回 image 就會還原 schema。

## Reboot 後自動啟動

`--restart unless-stopped` 可處理 process/container restart，但 rootless Podman 在 VM reboot
後是否自動啟動仍取決於使用者 service 與 linger。正式環境建議由維運人員依 VM 的
Podman 版本產生 systemd/Quadlet 設定，啟用 user service，並測試完整 reboot。

## 上線後檢核

- `/health` 回 200。
- 首頁可載入，BPMN 圖不是空白。
- 既有 release 仍可查詢。
- 建立測試 release 後可看到 `Approval gate`。
- `podman logs` 沒有 migration、permission 或 Python exception。
- 備份檔可在隔離環境開啟，並且不只存在同一顆 VM 磁碟。
