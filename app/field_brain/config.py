from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


APP_DIR_NAME = "FieldBrain"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / "field-brain"


def _looks_cloud_synced(path: Path) -> bool:
    cloud_markers = {"onedrive", "dropbox", "google drive", "icloud drive"}
    return any(part.casefold() in cloud_markers for part in path.parts)


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    db_path: Path
    originals_dir: Path
    backups_dir: Path
    demo: bool
    host: str
    port: int

    def ensure_directories(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)


def load_settings(*, create_directories: bool = True) -> Settings:
    """Load local settings without placing real business data beside the code."""

    configured_root = os.environ.get("FIELD_BRAIN_DATA_DIR", "").strip()
    data_root = Path(configured_root).expanduser() if configured_root else _default_data_root()
    data_root = data_root.resolve()

    demo = _truthy(os.environ.get("FIELD_BRAIN_DEMO"))
    if demo:
        data_root = data_root / "demo"

    allow_synced = _truthy(os.environ.get("FIELD_BRAIN_ALLOW_SYNCED_DATA"))
    if _looks_cloud_synced(data_root) and not allow_synced:
        raise RuntimeError(
            "실제 업무 데이터 폴더가 클라우드 동기화 위치로 보입니다. "
            "FIELD_BRAIN_DATA_DIR을 OneDrive 밖의 로컬 폴더로 바꿔 주세요."
        )

    host = os.environ.get("FIELD_BRAIN_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("첫 버전은 이 PC에서만 열 수 있도록 127.0.0.1로 실행해야 합니다.")

    try:
        port = int(os.environ.get("FIELD_BRAIN_PORT", "8765"))
    except ValueError as exc:
        raise RuntimeError("FIELD_BRAIN_PORT는 숫자여야 합니다.") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("FIELD_BRAIN_PORT는 1024에서 65535 사이여야 합니다.")

    settings = Settings(
        data_root=data_root,
        db_path=data_root / ("demo.db" if demo else "field-brain.db"),
        originals_dir=data_root / "originals",
        backups_dir=data_root / "backups",
        demo=demo,
        host=host,
        port=port,
    )
    if create_directories:
        settings.ensure_directories()
    return settings

