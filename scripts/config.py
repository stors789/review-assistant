# config.py
"""Lightweight environment-backed configuration helpers."""

from __future__ import annotations

import os


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_FLASH_MODEL = "deepseek-v4-flash"
DEFAULT_PRO_MODEL = "deepseek-v4-pro"
DEFAULT_STEP7_MODEL = DEFAULT_FLASH_MODEL


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def get_base_url(default: str = DEFAULT_BASE_URL) -> str:
    return env_str("REVIEW_ASSISTANT_BASE_URL", env_str("DEEPSEEK_BASE_URL", default)).rstrip("/")


def get_api_key() -> str:
    return env_str("DEEPSEEK_API_KEY", env_str("OPENAI_API_KEY", ""))


def get_model(default: str) -> str:
    return env_str("REVIEW_ASSISTANT_MODEL", default)


def get_step7_model(default: str = DEFAULT_STEP7_MODEL) -> str:
    return env_str("REVIEW_ASSISTANT_STEP7_MODEL", get_model(default))


def get_workers(default: int) -> int:
    workers = env_int("REVIEW_ASSISTANT_WORKERS", default)
    return max(1, workers)


def should_strip_proxy(default: bool = True) -> bool:
    if os.environ.get("REVIEW_ASSISTANT_USE_PROXY") is not None:
        return not env_bool("REVIEW_ASSISTANT_USE_PROXY", False)
    return env_bool("REVIEW_ASSISTANT_NO_PROXY", default)


def get_zotero_dir(default: str | None = None) -> str | None:
    value = env_str("ZOTERO_DIR", "")
    return value or default
