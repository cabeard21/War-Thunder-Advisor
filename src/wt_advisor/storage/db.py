"""Database construction and migration helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import Engine, Table, create_engine, event, inspect, text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.orm import Session, sessionmaker


def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(database: str | Path | URL = "wt-advisor.sqlite") -> Engine:
    """Build a SQLite engine without changing schema state."""

    if isinstance(database, URL):
        url: str | URL = database
    elif isinstance(database, Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{database.resolve().as_posix()}"
    elif database.startswith("sqlite:"):
        url = database
    else:
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path.resolve().as_posix()}"
    engine = create_engine(url, future=True)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _alembic_config(engine: Engine) -> Config:
    repository_root = Path(__file__).resolve().parents[3]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.attributes["connection"] = engine
    return config


def create_database(database: str | Path | URL = "wt-advisor.sqlite") -> Engine:
    """Create an engine and migrate its database to the latest schema."""

    engine = build_engine(database)
    try:
        command.upgrade(_alembic_config(engine), "head")
    except CommandError as exc:
        # Source checkouts retain the complete Alembic history. The wheel does
        # not ship those top-level scripts, so a new installed database is
        # initialized from the same SQLAlchemy schema and marked at head.
        # Never take this fallback for an existing database.
        if "Path doesn't exist" not in str(exc):
            raise
        from wt_advisor.storage.models import Base

        with engine.begin() as connection:
            if connection.dialect.has_table(connection, "alembic_version"):
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                if version == "0005":
                    return engine
                if version == "0004":
                    _upgrade_packaged_0004_to_0005(connection)
                    return engine
                raise
            Base.metadata.create_all(connection)
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0005')"))
    return engine


def _upgrade_packaged_0004_to_0005(connection: Connection) -> None:
    """Apply the packaged M3 delta when top-level Alembic scripts are absent."""

    from wt_advisor.storage.models import (
        AdvisorContextRow,
        LineupPresetRow,
        StoredEvaluationRow,
    )

    columns = {column["name"] for column in inspect(connection).get_columns("user_profiles")}
    if "write_revision" not in columns:
        connection.execute(
            text("ALTER TABLE user_profiles ADD COLUMN write_revision INTEGER NOT NULL DEFAULT 0")
        )
    tables: tuple[Table, ...] = (
        cast(Table, LineupPresetRow.__table__),
        cast(Table, StoredEvaluationRow.__table__),
        cast(Table, AdvisorContextRow.__table__),
    )
    for table in tables:
        table.create(connection, checkfirst=True)
    connection.execute(
        text("UPDATE alembic_version SET version_num = '0005' WHERE version_num = '0004'")
    )


@contextmanager
def database_session(engine: Engine) -> Iterator[Session]:
    """Provide a transaction-scoped SQLAlchemy session."""

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        yield session
