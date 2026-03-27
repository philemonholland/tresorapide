from __future__ import annotations

import os
from pathlib import Path


def read_env_value(name: str, default: str | None = None) -> str | None:
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(
                f"Could not read environment file for {name}: {file_path}"
            ) from exc
    return os.getenv(name, default)


def get_env(name: str, default: str | None = None) -> str:
    value = read_env_value(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_bool_env(name: str, default: bool = False) -> bool:
    value = read_env_value(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_int_env(name: str, default: int) -> int:
    value = read_env_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def get_list_env(name: str, default: list[str]) -> list[str]:
    raw_value = read_env_value(name)
    if raw_value is None:
        return default
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def get_path_env(name: str, default: Path) -> Path:
    value = read_env_value(name)
    if value is None:
        return default
    return Path(value)
