# 專案協作規範

## 每次修改都必須檢查與測試

- 修改前先閱讀相關程式、測試與文件；修正錯誤時先重現問題。
- 每次修改後都必須檢查差異，並執行與變更相關的靜態檢查及測試。
- 每次修改（包含文件、CI/CD、部署設定與測試本身）完成後，至少執行 `uv run test`；Python 相關變更則執行 `uv run quality`（已包含完整測試）。不得只跑靜態檢查或引用修改前的測試結果。
- 修改部署流程、移除腳本或替換工具時，必須同步更新相關測試，驗證目前流程的品質閘門、失敗處理與執行順序；不得只刪除斷言或跳過失敗測試。
- 若檢查或測試失敗，修正後必須重新驗證，不得直接宣告完成。
- 完成前執行 `git diff --check`，檢視 `git diff` 與 `git status --short`，包含新增檔案，避免混入無關變更。
- 回報修改內容、實際執行的檢查與測試結果；無法執行的項目需明確說明原因，不得視為通過。

## Python

- 使用 Python 3.10 以上版本，透過 `uv sync` 安裝鎖定的開發依賴。
- 首次執行完整測試前，先在 `frontend` 執行 `npm ci` 與 `npm run build`，產生 `app/static`；`tests/test_frontend.py` 需要實際的前端建置產物。
- 修改 CI 時，以乾淨 checkout 驗證建置前置條件；`.drone.yml` 必須讓 `frontend-quality` 成功後才執行 `python-quality`，不可依賴本機既有的 `app/static`。
- 在專案根目錄執行 `uv run lint`，檢查 Ruff 規則與格式。
- 使用 `uv run pytest tests/test_<相關模組>.py` 執行針對性測試。
- Python 程式、測試或測試設定修改完成後，執行 `uv run quality`，涵蓋 Ruff、格式檢查與完整 pytest；coverage 門檻為 85%。
- 涉及測試匯入或收集時，另執行 `uv run pytest --collect-only -q` 與 `uv run pytest`，驗證使用者直接呼叫 pytest 的入口。
- 保留 `tests/__init__.py`，讓 `tests.conftest` 與其他測試共用工具能透過套件路徑匯入。
- 測試使用暫存 SQLite 與模擬的外部服務，不依賴正式資料或真實 Drone／Gitea 憑證。

## 前端

- 前端變更在 `frontend` 目錄執行 `npm run quality`，涵蓋型別、lint、格式、測試、build 與 smoke check。
- 涉及操作流程或瀏覽器行為時，另執行 `npm run test:e2e`。

## 變更範圍與文件

- 保留使用者既有修改，不順手重構無關程式。
- 不提交 `.env`、憑證、本機資料庫或測試產物。
- 行為、設定或開發流程改變時，同步更新相關文件。
- 品質檢查細節參閱 `docs/quality-gates.md`；開發命令定義位於 `devtools/cli.py` 與 `frontend/package.json`。
