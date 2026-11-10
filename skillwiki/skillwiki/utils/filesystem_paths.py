"""Local filesystem path compatibility without process or global changes."""
from __future__ import annotations

import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    """Use Windows extended paths, including when children cross MAX_PATH."""
    if os.name != "nt":
        return path
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        return Path(value)
    # Normalize before prefixing: extended paths require absolute names and
    # disable Win32 normalization. Prefix short roots too: a child filename
    # may cross MAX_PATH even though its directory remains below the limit.
    value = os.path.abspath(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)
