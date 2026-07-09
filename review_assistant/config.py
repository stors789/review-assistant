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


def get_rerank_model(default: str = DEFAULT_FLASH_MODEL) -> str:
    return env_str("REVIEW_ASSISTANT_RERANK_MODEL", get_step7_model(default))


def get_workers(default: int) -> int:
    workers = env_int("REVIEW_ASSISTANT_WORKERS", default)
    return max(1, workers)


def get_temperature(default: float = 0.0) -> float:
    value = os.environ.get("REVIEW_ASSISTANT_TEMPERATURE")
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_reasoning_effort(default: str = "high") -> str:
    return env_str("REVIEW_ASSISTANT_REASONING_EFFORT", default)


def get_system_prompt_prefix(default: str = "") -> str:
    return env_str("REVIEW_ASSISTANT_SYSTEM_PROMPT_PREFIX", default)


def should_strip_proxy(default: bool = True) -> bool:
    if os.environ.get("REVIEW_ASSISTANT_USE_PROXY") is not None:
        return not env_bool("REVIEW_ASSISTANT_USE_PROXY", False)
    return env_bool("REVIEW_ASSISTANT_NO_PROXY", default)


def get_zotero_dir(default: str | None = None) -> str | None:
    value = env_str("ZOTERO_DIR", "")
    return value or default


def get_zotero_api_key() -> str:
    return env_str("ZOTERO_API_KEY", "")


def get_zotero_library_type(default: str = "user") -> str:
    return env_str("ZOTERO_LIBRARY_TYPE", default)


def get_zotero_library_id(default: str = "") -> str:
    return env_str("ZOTERO_LIBRARY_ID", default)


def get_zotero_web_import(default: bool = False) -> bool:
    return env_bool("ZOTERO_WEB_IMPORT", default)


def get_zotero_sync_timeout(default: int = 120) -> int:
    timeout = env_int("ZOTERO_SYNC_TIMEOUT", default)
    return max(0, timeout)


def get_linked_prefix_map() -> list[tuple[str, str]]:
    raw = env_str("ZOTERO_LINKED_PREFIX_MAP", "")
    if not raw:
        return []
    mappings = []
    for entry in raw.split("|"):
        entry = entry.strip()
        if "=>" not in entry:
            continue
        src, dst = entry.split("=>", 1)
        src = src.strip()
        dst = dst.strip()
        if src and dst:
            mappings.append((src, dst))
    return mappings
