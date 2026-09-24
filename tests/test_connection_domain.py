"""Connection entity 的規則測試——不建資料庫、不打上游。

這些規則原本散在 `ConnectionService.create` / `.update` 裡，要測「換 token 會不會
清掉舊的驗證結果」得先起一個 SQLite、發一個 HTTP 請求、再 monkeypatch 掉 Drone。

（domain 層不得 import framework 的結構不變式在 test_release_domain.py，
那兩個測試掃的是整個 app/domain，所以這裡的檔案也在保護範圍內。）
"""

from datetime import datetime, timezone

from app.domain.registry.entities import Connection, normalise_base_url
from app.domain.registry.value_objects import (
    ConnectionKind,
    ConnectionVerifyStatus,
    EncryptedToken,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc)
TOKEN = EncryptedToken(cipher_text="gAAAA-first", hint="…cdef")
NEW_TOKEN = EncryptedToken(cipher_text="gAAAA-second", hint="…wxyz")


def make_connection(*, is_default: bool = True) -> Connection:
    return Connection.create(
        kind=ConnectionKind.DRONE,
        name="default-drone",
        base_url="https://drone.example.com/",
        token=TOKEN,
        is_default=is_default,
        now=NOW,
    )


def test_a_trailing_slash_is_not_a_different_server() -> None:
    assert make_connection().base_url == "https://drone.example.com"
    assert normalise_base_url("https://drone.example.com///") == "https://drone.example.com"


def test_a_new_connection_has_not_been_verified() -> None:
    connection = make_connection()

    assert connection.verify_status is None
    assert connection.verified_at is None
    assert connection.token_hint == "…cdef"
    assert connection.created_at == NOW == connection.updated_at


def test_recording_a_probe_keeps_the_answer_off_the_hot_path() -> None:
    connection = make_connection()

    connection.record_probe(ConnectionVerifyStatus.UNAUTHORIZED, "rejected", at=LATER)

    assert connection.verify_status is ConnectionVerifyStatus.UNAUTHORIZED
    assert connection.verify_detail == "rejected"
    assert connection.verified_at == LATER
    assert connection.updated_at == LATER


def test_replacing_the_token_throws_away_the_old_verdict() -> None:
    connection = make_connection()
    connection.record_probe(ConnectionVerifyStatus.OK, None, at=NOW)

    connection.replace_token(NEW_TOKEN, at=LATER)

    # 留著 "ok" 等於拿上一把 token 的結論去描述一把還沒被試過的新 token。
    assert connection.verify_status is None
    assert connection.verify_detail is None
    assert connection.verified_at is None
    assert connection.token_encrypted == "gAAAA-second"
    assert connection.token_hint == "…wxyz"


def test_the_default_flag_moves_both_ways() -> None:
    connection = make_connection(is_default=False)

    connection.make_default(at=LATER)
    assert connection.is_default is True

    connection.clear_default(at=LATER)
    assert connection.is_default is False


def test_editing_stamps_the_row_even_when_nothing_changed() -> None:
    connection = make_connection()

    connection.touch(at=LATER)

    assert connection.updated_at == LATER


def test_renaming_and_repointing_do_not_touch_the_credential() -> None:
    connection = make_connection()
    connection.record_probe(ConnectionVerifyStatus.OK, None, at=NOW)

    connection.rename("primary-drone", at=LATER)
    connection.point_at("https://drone.internal/", at=LATER)

    assert connection.name == "primary-drone"
    assert connection.base_url == "https://drone.internal"
    # 換位址不是換憑證，之前的驗證結果仍然有意義。
    assert connection.verify_status is ConnectionVerifyStatus.OK


def test_identity_is_the_id_not_the_fields() -> None:
    one = make_connection()
    same_id = Connection(
        id=one.id,
        kind=ConnectionKind.GITEA,
        name="something-else",
        base_url="https://gitea.example.com",
        token=NEW_TOKEN,
        is_default=False,
        created_at=NOW,
        updated_at=NOW,
    )

    assert one == same_id
    assert one != make_connection()
    assert len({one, same_id}) == 1
