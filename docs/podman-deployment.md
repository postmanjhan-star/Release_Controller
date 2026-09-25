# 部署文件已移至 Portainer

目前部署方式請見 [Portainer 部署設定](deployment.md)，自動發布見 [Drone CI/CD](cicd.md)。

原本的 SSH / Podman 部署腳本已移除，本頁僅保留舊連結入口。
既有 Podman 環境遷移時，請先備份 SQLite 與 `APP_SECRET_KEY`，確認新 Stack 掛載正確資料目錄，
並避免新舊服務同時執行背景工作。備份與還原原則見[更新與維運](operations.md)。
