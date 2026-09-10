"""Immutable local storage for pasted source text.

Audio, message exports, and photos will use the same idea in later slices.  The
first slice stores pasted transcripts/messages as separate files so the event
summary can change without overwriting the source used to verify it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import re
import uuid


_KIND_PATTERN = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class StoredOriginal:
    path: Path
    relative_path: str
    sha256: str
    evidence_ref: str


def store_text_original(
    originals_dir: str | Path,
    text: str,
    *,
    kind: str = "note",
    now: datetime | None = None,
) -> StoredOriginal:
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("보존할 원문이 비어 있습니다.")
    if len(clean_text) > 200_000:
        raise ValueError("한 번에 저장할 원문은 20만 자를 넘을 수 없습니다.")

    root = Path(originals_dir).expanduser().resolve()
    moment = now or datetime.now().astimezone()
    safe_kind = _KIND_PATTERN.sub("-", kind.strip().lower()).strip("-") or "note"
    folder = (root / "text" / moment.strftime("%Y") / moment.strftime("%m")).resolve()
    if root != folder and root not in folder.parents:
        raise RuntimeError("원문 저장 경로가 지정된 폴더를 벗어났습니다.")
    folder.mkdir(parents=True, exist_ok=True)

    payload = (clean_text + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest().upper()
    filename = f"{moment.strftime('%Y%m%d-%H%M%S')}-{safe_kind}-{uuid.uuid4().hex[:12]}.txt"
    path = folder / filename
    with path.open("xb") as stream:
        stream.write(payload)
    try:
        path.chmod(0o600)
    except OSError:
        # Windows ACLs remain authoritative; chmod support varies by filesystem.
        pass

    relative = path.relative_to(root).as_posix()
    return StoredOriginal(
        path=path,
        relative_path=relative,
        sha256=digest,
        evidence_ref=f"local-original:{relative}:sha256:{digest}",
    )


def evidence_label(evidence_ref: str | None) -> str | None:
    if not evidence_ref:
        return None
    if evidence_ref.startswith("local-original:"):
        return "로컬에 보존된 원문"
    return "근거 연결됨"
