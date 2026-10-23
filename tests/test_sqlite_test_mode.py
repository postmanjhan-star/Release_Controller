"""The CI speedup must stay inside disposable databases and preserve transactions."""

import sqlite3
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine

from app.db.session import create_database_engine
from tests.conftest import configure_disposable_sqlite


def test_sync_override_is_confined_to_the_disposable_root(tmp_path):
    disposable = tmp_path / "disposable"
    disposable.mkdir()
    for filename, expected in ((disposable / "test.db", 0), (tmp_path / "durable.db", 2)):
        connection = sqlite3.connect(filename)
        try:
            connection.execute("PRAGMA synchronous = FULL")
            configure_disposable_sqlite(connection, disposable)
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == expected
        finally:
            connection.close()


def test_symlinks_cannot_opt_an_external_database_into_no_sync(tmp_path):
    disposable = tmp_path / "disposable"
    disposable.mkdir()
    external = tmp_path / "external.db"
    sqlite3.connect(external).close()
    alias = disposable / "alias.db"
    alias.symlink_to(external)
    connection = sqlite3.connect(alias)
    try:
        connection.execute("PRAGMA synchronous = FULL")
        configure_disposable_sqlite(connection, disposable)
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()


def test_non_sqlite_connections_are_untouched(tmp_path):
    # Any attempt to obtain a cursor on this object would fail.
    configure_disposable_sqlite(object(), tmp_path)


@pytest.mark.parametrize("factory", [create_database_engine, create_engine])
def test_option_applies_to_every_new_test_connection(factory, tmp_path, request):
    engine = factory(f"sqlite:///{tmp_path / 'connections.db'}")
    expected = 0 if request.config.getoption("--sqlite-no-sync") else 2
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == expected
    finally:
        engine.dispose()


def test_application_database_outside_pytest_root_stays_durable():
    with TemporaryDirectory(prefix="release-controller-durable-") as directory:
        engine = create_database_engine(f"sqlite:///{directory}/app.db")
        try:
            with engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        finally:
            engine.dispose()


def test_wal_foreign_keys_and_rollback_still_work(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'transactions.db'}")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
            connection.exec_driver_sql("CREATE TABLE sample (value INTEGER)")
            connection.commit()
            connection.exec_driver_sql("INSERT INTO sample VALUES (42)")
            connection.rollback()
            assert connection.exec_driver_sql("SELECT count(*) FROM sample").scalar_one() == 0
    finally:
        engine.dispose()
