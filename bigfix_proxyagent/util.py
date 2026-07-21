"""Small shared helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_text_atomic(path: Path, text: str) -> None:
    """Write text via a temp file + rename so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON via a temp file + rename so readers never see a partial file."""
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def major_minor(version: str | None) -> tuple[int, int] | None:
    """The ``(major, minor)`` of a version string, or ``None`` if absent or not
    of the form ``<int>.<int>[...]`` (e.g. a dev/rc suffix on the minor).

    Useful for a plugin that forces a fresh check after its own version
    changes across a meaningful boundary.
    """
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None
