"""上游連線 aggregate 的業務例外。

Domain 層：不得 import framework（SQLAlchemy / FastAPI / Pydantic）。
"""


class ConnectionNotFoundError(Exception):
    pass


class ConnectionNameTakenError(Exception):
    """連線名稱已經有人用了。

    真正的保證是 upstream_connections.name 的唯一條件；repository 把資料庫
    拒絕的結果翻成這個例外。
    """


class ConnectionInUseError(Exception):
    """還有 project 或 component 指著這條連線。"""


class NoDefaultConnectionError(Exception):
    """沒有指定覆寫，而這個種類也沒有預設連線。"""
