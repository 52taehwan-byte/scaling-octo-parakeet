from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import sqlite3
import uuid


@dataclass(frozen=True, slots=True)
class BackupInfo:
    path: Path
    byte_size: int
    sha256: str
    created_at: datetime


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def verify_backup(path: str | Path, *, expected_sha256: str | None = None) -> BackupInfo:
    backup_path = Path(path).resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"백업 파일을 찾을 수 없습니다: {backup_path}")

    connection = _read_only_connection(backup_path)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError(f"백업 무결성 검사에 실패했습니다: {integrity}")
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_issues:
            raise RuntimeError(
                f"백업에서 끊어진 데이터 연결 {len(foreign_key_issues)}건을 발견했습니다."
            )
    finally:
        connection.close()

    digest = _sha256(backup_path)
    if expected_sha256 and digest.casefold() != expected_sha256.strip().casefold():
        raise RuntimeError("백업 파일 해시가 기록된 값과 다릅니다.")

    stat = backup_path.stat()
    return BackupInfo(
        path=backup_path,
        byte_size=stat.st_size,
        sha256=digest,
        created_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
    )


def create_backup(
    db_path: str | Path,
    backups_dir: str | Path,
    *,
    now: datetime | None = None,
) -> BackupInfo:
    """Create and verify one consistent SQLite snapshot.

    The source database is never copied with a plain filesystem copy while it
    is open. SQLite's backup API creates a transactionally consistent image.
    """

    source_path = Path(db_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"원장 파일을 찾을 수 없습니다: {source_path}")

    destination_dir = Path(backups_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    final_path = destination_dir / f"field-brain-{timestamp}.db"
    if final_path.exists():
        final_path = destination_dir / f"field-brain-{timestamp}-{uuid.uuid4().hex[:8]}.db"
    temporary_path = destination_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp"

    source = sqlite3.connect(source_path, timeout=5.0)
    destination = sqlite3.connect(temporary_path)
    try:
        source.backup(destination)
        destination.commit()
    except Exception:
        destination.close()
        source.close()
        temporary_path.unlink(missing_ok=True)
        raise
    else:
        destination.close()
        source.close()

    try:
        verify_backup(temporary_path)
        os.replace(temporary_path, final_path)
        return verify_backup(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def list_backups(backups_dir: str | Path) -> list[BackupInfo]:
    directory = Path(backups_dir).resolve()
    if not directory.is_dir():
        return []
    results: list[BackupInfo] = []
    for path in sorted(directory.glob("field-brain-*.db"), reverse=True):
        try:
            results.append(verify_backup(path))
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            continue
    return results

