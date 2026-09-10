"""Create a local, read-only migration preview from the retired static MVP."""

from __future__ import annotations

import argparse
from pathlib import Path

from field_brain.config import load_settings
from field_brain.migration_preview import write_preview


def main() -> None:
    parser = argparse.ArgumentParser(description="Field Brain 과거 자료 이관 미리보기")
    parser.add_argument("source", type=Path, help="과거 정적 MVP app.js 경로")
    parser.add_argument("--output", type=Path, help="미리보기 JSON 저장 경로")
    args = parser.parse_args()
    settings = load_settings()
    output = args.output or settings.data_root / "migration-previews" / "codex-static-preview.json"
    written = write_preview(args.source, output)
    print(f"이관 미리보기를 만들었습니다: {written}")
    print("실제 Field Brain 장부에는 아무것도 저장하지 않았습니다.")


if __name__ == "__main__":
    main()
