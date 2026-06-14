# llm_client.py
"""
Thread-safe client pool and wrappers for LLM calls with key rotation and retry logic.
"""

import os
import re
import json
import itertools
import threading
from openai import OpenAI

_key_cycle = None
_key_lock = threading.Lock()
_base_url = "https://api.deepseek.com"


def init_client_pool(base_url="https://api.deepseek.com"):
    """Initialize the rotation key pool and clear proxy settings."""
    global _key_cycle, _base_url
    _base_url = base_url

    # Strip proxy variables to bypass local proxies
    for v in ("all_proxy", "ALL_PROXY", "socks_proxy", "SOCKS_PROXY", "socks5_proxy", "SOCKS5_PROXY"):
        os.environ.pop(v, None)

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")

    api_keys = [api_key]
    for i in range(2, 20):
        k = os.environ.get(f"DEEPSEEK_API_KEY_{i}")
        if k:
            api_keys.append(k)
        else:
            break

    _key_cycle = itertools.cycle(api_keys)


def get_client() -> OpenAI:
    """Get a thread-safe OpenAI client rotating through configured API keys."""
    global _key_cycle, _base_url
    if _key_cycle is None:
        init_client_pool()
    with _key_lock:
        key = next(_key_cycle)
    return OpenAI(api_key=key, base_url=_base_url)


def call_json(client: OpenAI, system: str, user: str, model: str, max_tokens: int = 4096, retries: int = 2) -> dict:
    """JSON extraction using pro thinking mode, prompt constraints, and robust regex fallback parsing."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=max_tokens,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("API returned empty response")
            content = content.strip()
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                m = re.search(r'\{.*\}', content, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
                raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"     ⚠ 重试 {attempt+1}/{retries}: {e}", flush=True)
    raise last_err


def call_json_light(client: OpenAI, system: str, user: str, model: str = "deepseek-v4-flash",
                    max_tokens: int = 16384, retries: int = 2) -> dict:
    """Lightweight JSON extraction without reasoning, suitable for validation to prevent output truncation."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("API returned empty response")
            content = content.strip()
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                m = re.search(r'\{.*\}', content, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
                raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"     ⚠ 重试 {attempt+1}/{retries}: {e}", flush=True)
    raise last_err


def call_text(client: OpenAI, prompt: str, model: str, max_tokens: int = 4096, retries: int = 2,
              temperature: float = 0) -> str:
    """Text generation wrapper using reasoning (pro) mode. Default temperature=0."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("API returned empty response")
            return content
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"     ⚠ 重试 {attempt+1}/{retries}: {e}", flush=True)
    raise last_err
