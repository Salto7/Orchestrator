#!/usr/bin/env python3
"""hello-world skill script — runs inside the Docker sandbox."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    path = Path("/workspace/hello.txt")
    path.write_text(
        f"hello from orchestrator at {datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )
    print(path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
