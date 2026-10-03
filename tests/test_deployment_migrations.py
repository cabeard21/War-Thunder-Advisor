"""Persistence and failure boundaries for source-image and local wheel upgrades."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

from wt_advisor.storage import db


@pytest.mark.parametrize("revision", ["0002", "0004", "0005", "0006", "0007", "0008"])
def test_retained_migrations_preserve_existing_data(tmp_path: Path, revision: str) -> None:
    location = tmp_path / "mounted" / "advisor.sqlite"
    engine = db.build_engine(location)
    configuration = db._alembic_config(engine)
    command.upgrade(configuration, revision)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE deployment_marker (value TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO deployment_marker VALUES ('retained')")
    engine.dispose()

    upgraded = db.create_database(location)
    with upgraded.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT value FROM deployment_marker").scalar() == "retained"
        )
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == (
            ScriptDirectory.from_config(configuration).get_current_head()
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    upgraded.dispose()


@pytest.mark.parametrize("revision", ["0004", "0005", "0006", "0007", "0008"])
def test_local_wheel_upgrade_preserves_data_when_scripts_are_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
) -> None:
    location = tmp_path / "mounted" / "advisor.sqlite"
    engine = db.build_engine(location)
    command.upgrade(db._alembic_config(engine), revision)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE deployment_marker (value TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO deployment_marker VALUES ('retained')")
    engine.dispose()
    monkeypatch.setattr(
        db.command,
        "upgrade",
        Mock(
            side_effect=CommandError(
                "Path doesn't exist: migrations",
            )
        ),
    )

    upgraded = db.create_database(location)
    with upgraded.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT value FROM deployment_marker").scalar() == "retained"
        )
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0008"
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    upgraded.dispose()


def test_migration_error_is_not_reinterpreted_as_fresh_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = tmp_path / "mounted" / "advisor.sqlite"
    monkeypatch.setattr(db.command, "upgrade", Mock(side_effect=CommandError("migration failed")))
    with pytest.raises(CommandError, match="migration failed"):
        db.create_database(location)
    engine = db.build_engine(location)
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).all()
            == []
        )
    engine.dispose()


def test_local_wheel_refuses_unsupported_legacy_schema_without_modifying_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = tmp_path / "mounted" / "advisor.sqlite"
    engine = db.build_engine(location)
    command.upgrade(db._alembic_config(engine), "0002")
    engine.dispose()
    monkeypatch.setattr(
        db.command,
        "upgrade",
        Mock(
            side_effect=CommandError(
                "Path doesn't exist: migrations",
            )
        ),
    )
    with pytest.raises(CommandError, match="Path doesn't exist"):
        db.create_database(location)
    retained = db.build_engine(location)
    with retained.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0002"
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    retained.dispose()


@pytest.mark.parametrize("revision", ["0004", "0005"])
def test_local_wheel_resumes_additive_columns_without_duplicate_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
) -> None:
    location = tmp_path / "mounted" / "partial.sqlite"
    engine = db.build_engine(location)
    command.upgrade(db._alembic_config(engine), revision)
    with engine.begin() as connection:
        if revision == "0004":
            connection.exec_driver_sql(
                "ALTER TABLE user_profiles ADD COLUMN write_revision INTEGER NOT NULL DEFAULT 0",
            )
        else:
            connection.exec_driver_sql(
                "ALTER TABLE capability_observations ADD COLUMN verified_at DATE",
            )
            connection.exec_driver_sql(
                "ALTER TABLE capability_observations ADD COLUMN source_revision VARCHAR(160)",
            )
    engine.dispose()
    monkeypatch.setattr(
        db.command,
        "upgrade",
        Mock(
            side_effect=CommandError(
                "Path doesn't exist: migrations",
            )
        ),
    )
    upgraded = db.create_database(location)
    with upgraded.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0008"
        )
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    upgraded.dispose()
