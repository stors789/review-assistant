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
from config import DEFAULT_BASE_URL, DEFAULT_FLASH_MODEL, get_api_key, should_strip_proxy
from errors import LLMCallError


class ClientPool:
    """Thread-safe pool managing LLM API key rotation and embedding client lifecycle."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str | None = None,
                 strip_proxy: bool | None = None):
        self._base_url = base_url
        self._key_lock = threading.Lock()
        self._emb_lock = threading.Lock()
        self._emb_client: OpenAI | None = None
        self._key_cycle = None

        if strip_proxy is None:
            strip_proxy = should_strip_proxy()
        if strip_proxy:
            for v in (
                "all_proxy", "ALL_PROXY", "socks_proxy", "SOCKS_PROXY", "socks5_proxy", "SOCKS5_PROXY",
                "http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"
            ):
                os.environ.pop(v, None)

        if api_key:
            api_keys = [api_key]
        else:
            api_key = get_api_key()
            if not api_key:
                raise ValueError("请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")

            api_keys = [api_key]
            for i in range(2, 20):
                k = os.environ.get(f"DEEPSEEK_API_KEY_{i}")
                if k:
                    api_keys.append(k)
                else:
                    break

        self._key_cycle = itertools.cycle(api_keys)

    def get_client(self) -> OpenAI:
        """Get a thread-safe OpenAI client rotating through configured API keys."""
        if self._key_cycle is None:
            raise RuntimeError("ClientPool not initialized")
        with self._key_lock:
            key = next(self._key_cycle)
        return OpenAI(api_key=key, base_url=self._base_url)

    def get_embedding_client(self) -> OpenAI:
        """Get a thread-safe OpenAI client dedicated to embedding generation."""
        with self._emb_lock:
            if self._emb_client is not None:
                return self._emb_client

            emb_key = os.environ.get("REVIEW_ASSISTANT_EMBEDDING_API_KEY")
            emb_url = os.environ.get("REVIEW_ASSISTANT_EMBEDDING_BASE_URL")

            if not emb_key:
                emb_key = os.environ.get("OPENAI_API_KEY")
            if not emb_key:
                emb_key = get_api_key()

            if not emb_url:
                if os.environ.get("OPENAI_API_KEY") == emb_key:
                    emb_url = "https://api.openai.com/v1"
                else:
                    emb_url = self._base_url

            if not emb_key:
                raise ValueError("请设置 REVIEW_ASSISTANT_EMBEDDING_API_KEY 或 OPENAI_API_KEY 用于生成向量嵌入")

            self._emb_client = OpenAI(api_key=emb_key, base_url=emb_url)
            return self._emb_client


_default_pool: ClientPool | None = None


def init_client_pool(base_url=DEFAULT_BASE_URL, api_key=None, strip_proxy=None) -> ClientPool:
    """Initialize the default ClientPool for key rotation and return it."""
    global _default_pool
    _default_pool = ClientPool(base_url=base_url, api_key=api_key, strip_proxy=strip_proxy)
    return _default_pool


def get_client(pool: ClientPool | None = None) -> OpenAI:
    """Get a thread-safe OpenAI client rotating through configured API keys."""
    global _default_pool
    if pool is not None:
        return pool.get_client()
    if _default_pool is None:
        init_client_pool()
    return _default_pool.get_client()


def get_embedding_client(pool: ClientPool | None = None) -> OpenAI:
    """Get a thread-safe OpenAI client dedicated to embedding generation."""
    global _default_pool
    if pool is not None:
        return pool.get_embedding_client()
    if _default_pool is None:
        init_client_pool()
    return _default_pool.get_embedding_client()


def reset_client_pool() -> None:
    """Reset the default client pool for test cleanup."""
    global _default_pool
    _default_pool = None


def call_json(client: OpenAI, system: str, user: str, model: str, max_tokens: int = 4096, retries: int = 2) -> dict:
    """JSON extraction using pro thinking mode, prompt constraints, and robust regex fallback parsing."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
            }
            # Only add reasoning parameters if model name indicates reasoning capability
            is_openai_reasoning = any(x in model.lower() for x in ("o1", "o3"))
            is_deepseek_reasoning = any(x in model.lower() for x in ("reasoner", "r1", "pro"))
            
            if is_openai_reasoning:
                kwargs["reasoning_effort"] = "high"
                if "temperature" in kwargs:
                    del kwargs["temperature"]
            elif is_deepseek_reasoning:
                kwargs["reasoning_effort"] = "high"
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as e:
                err_msg = str(e).lower()
                # If provider does not support these extra parameters, strip them and retry immediately
                if any(p in err_msg for p in ("extra_body", "thinking", "reasoning_effort", "unrecognized", "unknown parameter", "extra parameter", "unexpected keyword")):
                    kwargs.pop("reasoning_effort", None)
                    kwargs.pop("extra_body", None)
                    if "temperature" not in kwargs:
                        kwargs["temperature"] = 0
                    resp = client.chat.completions.create(**kwargs)
                else:
                    raise

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
    raise LLMCallError(
        str(last_err), original=last_err, attempts=retries + 1
    ) from last_err


def call_json_light(client: OpenAI, system: str, user: str, model: str = DEFAULT_FLASH_MODEL,
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
    raise LLMCallError(
        str(last_err), original=last_err, attempts=retries + 1
    ) from last_err


def call_text(client: OpenAI, prompt: str, model: str, max_tokens: int = 4096, retries: int = 2,
              temperature: float = 0) -> str:
    """Text generation wrapper using reasoning (pro) mode. Default temperature=0."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            # Only add reasoning parameters if model name indicates reasoning capability
            is_openai_reasoning = any(x in model.lower() for x in ("o1", "o3"))
            is_deepseek_reasoning = any(x in model.lower() for x in ("reasoner", "r1", "pro"))

            if is_openai_reasoning:
                kwargs["reasoning_effort"] = "high"
                if "temperature" in kwargs:
                    del kwargs["temperature"]
            elif is_deepseek_reasoning:
                kwargs["reasoning_effort"] = "high"
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as e:
                err_msg = str(e).lower()
                # If provider does not support these extra parameters, strip them and retry immediately
                if any(p in err_msg for p in ("extra_body", "thinking", "reasoning_effort", "unrecognized", "unknown parameter", "extra parameter", "unexpected keyword")):
                    kwargs.pop("reasoning_effort", None)
                    kwargs.pop("extra_body", None)
                    if "temperature" not in kwargs:
                        kwargs["temperature"] = temperature
                    resp = client.chat.completions.create(**kwargs)
                else:
                    raise

            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("API returned empty response")
            return content
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"     ⚠ 重试 {attempt+1}/{retries}: {e}", flush=True)
    raise LLMCallError(
        str(last_err), original=last_err, attempts=retries + 1
    ) from last_err


def get_embedding(client: OpenAI, text: str, model: str = "text-embedding-3-small", retries: int = 2) -> list[float]:
    """Get embedding vector for a given text block, using text-embedding-3-small by default."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.embeddings.create(input=[text], model=model)
            return resp.data[0].embedding
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"     ⚠ Embedding 生成重试 {attempt+1}/{retries}: {e}", flush=True)
    raise LLMCallError(
        str(last_err), original=last_err, attempts=retries + 1
    ) from last_err
