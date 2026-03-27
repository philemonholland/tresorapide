from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_default_backup_root(project_root: Path) -> Path:
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Tresorapide" / "backups"
    return project_root / "backups"


def ensure_protected_directory(path: Path, *, exist_ok: bool = True) -> bool:
    path.mkdir(parents=True, exist_ok=exist_ok)
    if os.name != "nt":
        return False

    try:
        subprocess.run(
            ["cipher.exe", "/E", "/A", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError):
        return False
    return True
