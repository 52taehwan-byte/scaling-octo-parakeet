"""SQLite connection and versioned migration support.

Callers must provide the database path.  This keeps real business data out of
the code/OneDrive tree unless the caller explicitly chooses otherwise.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class DatabaseVersionError(RuntimeError):
    pass


def connect(path: str | Path) -> sqlite3.Connection:
    """Open one configured connection.

    Foreign-key enforcement is connection-local in SQLite, so it is enabled and
    verified every time. WAL allows the local UI to read while a short write is
    being committed.
    """

    db_path = Path(path).expanduser()
    if str(db_path).strip() == "":
        raise ValueError("database path is required")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    enabled = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    if enabled != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
    return connection


def _migration_files(migrations_dir: str | Path | None = None) -> list[tuple[int, Path]]:
    directory = Path(migrations_dir) if migrations_dir is not None else DEFAULT_MIGRATIONS_DIR
    if not directory.is_dir():
        raise DatabaseVersionError(f"migration directory does not exist: {directory}")

    result: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if match:
            result.append((int(match.group(1)), path))
    versions = [version for version, _ in result]
    expected = list(range(1, len(versions) + 1))
    if not versions or versions != expected:
        raise DatabaseVersionError(
            f"migration versions must be consecutive from 0001; found {versions}"
        )
    return result


def schema_version(path: str | Path) -> int:
    with closing(connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(
    path: str | Path,
    *,
    migrations_dir: str | Path | None = None,
    target_version: int | None = None,
) -> int:
    """Migrate a database forward and return its resulting ``user_version``."""

    migrations = _migration_files(migrations_dir)
    latest = migrations[-1][0]
    target = latest if target_version is None else target_version
    if target < 0 or target > latest:
        raise DatabaseVersionError(f"unsupported target schema version: {target}")

    connection = connect(path)
    try:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > latest:
            raise DatabaseVersionError(
                f"database schema {current} is newer than this app supports ({latest})"
            )
        if target < current:
            raise DatabaseVersionError("schema downgrades are not supported")

        for version, migration_path in migrations:
            if version <= current or version > target:
                continue
            sql = migration_path.read_text(encoding="utf-8")
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nPRAGMA user_version = {version};\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            current = version

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign-key violations after migration: {len(violations)}")
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
        return current
    finally:
        connection.close()


initialize = migrate


@contextmanager
def transaction(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Run one business save plus its audit rows atomically."""

    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
