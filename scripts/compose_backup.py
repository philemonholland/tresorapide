#!/usr/bin/env python
"""Create a timestamped Docker Compose backup for database and media data."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

try:
    from scripts.host_storage_security import (
        ensure_protected_directory,
        get_default_backup_root,
    )
except ImportError:  # pragma: no cover - direct script execution path
    from host_storage_security import ensure_protected_directory, get_default_backup_root

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_ROOT = get_default_backup_root(PROJECT_ROOT)
TEMP_MEDIA_ARCHIVE = "/tmp/tresorapide-backup/media.tar.gz"

CREATE_MEDIA_ARCHIVE_SCRIPT = """
from pathlib import Path
import tarfile

root = Path("/data/media")
archive = Path("/tmp/tresorapide-backup/media.tar.gz")
root.mkdir(parents=True, exist_ok=True)
archive.parent.mkdir(parents=True, exist_ok=True)

with tarfile.open(archive, "w:gz") as tar:
    for item in sorted(root.rglob("*")):
        tar.add(item, arcname=item.relative_to(root))
"""


def run(
    command: list[str],
    *,
    stdout: TextIO | None = None,
    capture_output: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command from the project root and raise on failure."""
    print(f"+ {' '.join(command)}")
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        stdout=stdout,
        stderr=subprocess.STDOUT if stdout is not None else None,
        capture_output=capture_output,
        text=text,
    )


def wait_for_exec(service: str, command: list[str], timeout: int = 60) -> None:
    """Wait until a service can successfully run the given command."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            run(["docker", "compose", "exec", "-T", service, *command], capture_output=True)
        except subprocess.CalledProcessError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
        else:
            return


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Back up the Compose-managed PostgreSQL database and media volume.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_BACKUP_ROOT),
        help="Directory that receives timestamped backup folders.",
    )
    parser.add_argument(
        "--allow-unprotected-storage",
        action="store_true",
        help="Allow writing backups without Windows at-rest protection when EFS is unavailable.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the backup workflow."""
    args = parse_args()
    backup_root = Path(args.output_root).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = backup_root / timestamp
    database_path = backup_dir / "database.sql"
    media_path = backup_dir / "media.tar.gz"
    metadata_path = backup_dir / "backup-metadata.json"

    protected_storage = ensure_protected_directory(backup_dir, exist_ok=False)
    if not protected_storage and not args.allow_unprotected_storage:
        raise RuntimeError(
            "Refusing to create a plaintext backup on this platform. "
            "Re-run with --allow-unprotected-storage only if you already have "
            "another trusted at-rest protection mechanism."
        )

    run(["docker", "compose", "up", "-d", "db", "web"])
    wait_for_exec(
        "db",
        ["sh", "-c", 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
    )
    wait_for_exec("web", ["python", "-c", "from pathlib import Path; Path('/data/media').mkdir(parents=True, exist_ok=True)"])

    with database_path.open("w", encoding="utf-8", newline="\n") as handle:
        run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --create --no-owner --no-privileges',
            ],
            stdout=handle,
        )

    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            CREATE_MEDIA_ARCHIVE_SCRIPT,
        ]
    )
    run(["docker", "compose", "cp", f"web:{TEMP_MEDIA_ARCHIVE}", str(media_path)])
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            "from pathlib import Path; Path('/tmp/tresorapide-backup/media.tar.gz').unlink(missing_ok=True)",
        ]
    )

    metadata_path.write_text(
        json.dumps(
            {
                "format_version": 2,
                "created_at": timestamp,
                "database_file": database_path.name,
                "media_file": media_path.name,
                "secrets_included": False,
                "protected_storage": protected_storage,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    print("")
    print("Backup completed.")
    print(f"  Folder: {backup_dir}")
    print(f"  Database: {database_path.name}")
    print(f"  Media: {media_path.name}")
    print(f"  Metadata: {metadata_path.name}")
    print(f"  Protected at rest: {'yes' if protected_storage else 'no'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Backup failed with exit code {exc.returncode}.", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
    except RuntimeError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
