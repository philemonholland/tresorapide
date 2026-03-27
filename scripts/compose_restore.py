#!/usr/bin/env python
"""Restore a Docker Compose backup and verify the restored stack."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_DATABASE_DUMP = "/tmp/tresorapide-restore/database.sql"
TEMP_MEDIA_ARCHIVE = "/tmp/tresorapide-restore/media.tar.gz"

RESTORE_MEDIA_ARCHIVE_SCRIPT = """
from pathlib import Path
import shutil
import tarfile

root = Path("/data/media")
archive = Path("/tmp/tresorapide-restore/media.tar.gz")
root.mkdir(parents=True, exist_ok=True)

for item in root.iterdir():
    if item.is_dir():
        shutil.rmtree(item)
    else:
        item.unlink()

with tarfile.open(archive, "r:gz") as tar:
    destination = root.resolve()
    for member in tar.getmembers():
        target = (destination / member.name).resolve()
        if target != destination and destination not in target.parents:
            raise RuntimeError(f"Refusing to restore unsafe path: {member.name}")
        if member.issym() or member.islnk():
            raise RuntimeError(f"Refusing to restore linked path: {member.name}")
    tar.extractall(destination, filter="data")

archive.unlink(missing_ok=True)
"""

VERIFY_SUMMARY_SCRIPT = """
from django.conf import settings
from django.contrib.auth import get_user_model
from pathlib import Path

media_files = sum(1 for path in Path(settings.MEDIA_ROOT).rglob("*") if path.is_file())
print(f"users={get_user_model().objects.count()} media_files={media_files}")
"""

VERIFY_HTTP_SCRIPT = """
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/api/ready/", timeout=10) as response:
    print(json.dumps(json.load(response), sort_keys=True))
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


def wait_for_exec(service: str, command: list[str], timeout: int = 90) -> None:
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
        description="Restore a timestamped Compose backup and verify the stack.",
    )
    parser.add_argument(
        "backup_dir",
        help="Path to a backup folder that contains database.sql and media.tar.gz.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the restore workflow."""
    args = parse_args()
    backup_dir = Path(args.backup_dir).expanduser().resolve()
    database_path = backup_dir / "database.sql"
    media_path = backup_dir / "media.tar.gz"
    metadata_path = backup_dir / "backup-metadata.json"
    metadata = None

    if not database_path.exists():
        raise FileNotFoundError(f"Missing database dump: {database_path}")
    if not media_path.exists():
        raise FileNotFoundError(f"Missing media archive: {media_path}")
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("secrets_included") is False:
            print(
                "Note: this backup excludes secrets. "
                "Make sure the current secret store and .env configuration are already correct."
            )

    run(["docker", "compose", "up", "-d", "db"])
    wait_for_exec(
        "db",
        ["sh", "-c", 'pg_isready -U "$POSTGRES_USER" -d postgres'],
    )
    run(["docker", "compose", "exec", "-T", "db", "sh", "-c", "mkdir -p /tmp/tresorapide-restore"])
    run(["docker", "compose", "cp", str(database_path), f"db:{TEMP_DATABASE_DUMP}"])
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -f /tmp/tresorapide-restore/database.sql',
        ]
    )
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            "rm -f /tmp/tresorapide-restore/database.sql",
        ]
    )

    run(["docker", "compose", "up", "-d", "web"])
    wait_for_exec("web", ["python", "-c", "from pathlib import Path; Path('/data/media').mkdir(parents=True, exist_ok=True)"])
    run(["docker", "compose", "exec", "-T", "web", "sh", "-c", "mkdir -p /tmp/tresorapide-restore"])
    run(["docker", "compose", "cp", str(media_path), f"web:{TEMP_MEDIA_ARCHIVE}"])
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            RESTORE_MEDIA_ARCHIVE_SCRIPT,
        ]
    )
    run(["docker", "compose", "exec", "-T", "web", "python", "manage.py", "collectstatic", "--noinput"])
    run(["docker", "compose", "exec", "-T", "web", "python", "manage.py", "check"])
    wait_for_exec("web", ["python", "-c", VERIFY_HTTP_SCRIPT])
    run(["docker", "compose", "exec", "-T", "web", "python", "-c", VERIFY_HTTP_SCRIPT])
    run(["docker", "compose", "exec", "-T", "web", "python", "manage.py", "shell", "-c", VERIFY_SUMMARY_SCRIPT])

    print("")
    print("Restore completed and verified.")
    print(f"  Source folder: {backup_dir}")
    print("  Checks: Django check, readiness endpoint, user/media summary")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
