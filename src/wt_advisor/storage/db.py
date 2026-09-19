"""Database construction and migration helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
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
    command.upgrade(_alembic_config(engine), "head")
    return engine


@contextmanager
def database_session(engine: Engine) -> Iterator[Session]:
    """Provide a transaction-scoped SQLAlchemy session."""

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        yield session
