from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from config.env_helpers import get_env, get_int_env, get_list_env
from scripts.host_storage_security import ensure_protected_directory, get_default_backup_root


class FileBackedEnvironmentHelperTests(SimpleTestCase):
    def test_get_env_prefers_file_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            secret_path = Path(temp_dir) / "secret.txt"
            secret_path.write_text("top-secret\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"SAMPLE_VALUE": "raw-value", "SAMPLE_VALUE_FILE": str(secret_path)},
                clear=True,
            ):
                self.assertEqual(get_env("SAMPLE_VALUE"), "top-secret")

    def test_get_int_env_reads_file_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            int_path = Path(temp_dir) / "port.txt"
            int_path.write_text("5432\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"POSTGRES_PORT_FILE": str(int_path)},
                clear=True,
            ):
                self.assertEqual(get_int_env("POSTGRES_PORT", 0), 5432)

    def test_get_list_env_reads_file_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            list_path = Path(temp_dir) / "hosts.txt"
            list_path.write_text("localhost, 127.0.0.1 ,web\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"DJANGO_ALLOWED_HOSTS_FILE": str(list_path)},
                clear=True,
            ):
                self.assertEqual(
                    get_list_env("DJANGO_ALLOWED_HOSTS", []),
                    ["localhost", "127.0.0.1", "web"],
                )

    def test_get_env_raises_when_file_cannot_be_read(self) -> None:
        with patch.dict(
            os.environ,
            {"MISSING_SECRET_FILE": str(Path("Z:\\does-not-exist\\secret.txt"))},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                get_env("MISSING_SECRET")


class HostStorageSecurityTests(SimpleTestCase):
    def test_default_backup_root_prefers_localappdata_on_windows(self) -> None:
        with patch("scripts.host_storage_security.os.name", "nt"):
            with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}, clear=True):
                root = get_default_backup_root(Path(r"D:\Repo"))
        self.assertEqual(root, Path(r"C:\Users\Test\AppData\Local") / "Tresorapide" / "backups")

    def test_ensure_protected_directory_uses_windows_efs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protected_dir = Path(temp_dir) / "secure"
            with patch("scripts.host_storage_security.os.name", "nt"):
                with patch("scripts.host_storage_security.subprocess.run") as mocked_run:
                    self.assertTrue(ensure_protected_directory(protected_dir))
                    mocked_run.assert_called_once()
                    self.assertTrue(protected_dir.exists())

    def test_ensure_protected_directory_returns_false_when_cipher_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protected_dir = Path(temp_dir) / "secure"
            with patch("scripts.host_storage_security.os.name", "nt"):
                with patch(
                    "scripts.host_storage_security.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, ["cipher.exe"]),
                ):
                    self.assertFalse(ensure_protected_directory(protected_dir))
                    self.assertTrue(protected_dir.exists())

    def test_ensure_protected_directory_returns_false_off_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protected_dir = Path(temp_dir) / "secure"
            with patch("scripts.host_storage_security.os.name", "posix"):
                self.assertFalse(ensure_protected_directory(protected_dir))
                self.assertTrue(protected_dir.exists())

    def test_ensure_protected_directory_can_reject_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protected_dir = Path(temp_dir) / "secure"
            protected_dir.mkdir()
            with patch("scripts.host_storage_security.os.name", "posix"):
                with self.assertRaises(FileExistsError):
                    ensure_protected_directory(protected_dir, exist_ok=False)
