from __future__ import annotations

import threading
import webbrowser

import uvicorn

from field_brain.config import load_settings


def main() -> None:
    settings = load_settings()
    url = f"http://{settings.host}:{settings.port}"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "field_brain.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

