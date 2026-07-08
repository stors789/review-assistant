# caching.py
"""
Caching system logic for all steps of the literature review synthesis pipeline.
"""

import json
import hashlib
from pathlib import Path
from utils import file_sha256
import sys

# Cache Versions
FINDINGS_CACHE_VERSION = "2026-06-14-v4"
OUTLINE_CACHE_VERSION = "2026-06-14-v2"
SECTIONS_CACHE_VERSION = "2026-06-14-v1"
REPORT_CACHE_VERSION = "2026-06-14-v1"


def findings_cache_key(pdf_path: Path, question: str, model: str,
                       use_evidence_pack: bool = True,
                       ai_rerank_chunks: bool = False,
                       use_vector_search: bool = False) -> str:
    """Generate a stable findings cache key based on settings and file hash."""
    payload = {
        "cache_version": FINDINGS_CACHE_VERSION,
        "pdf_sha256": file_sha256(pdf_path),
        "question": question,
        "model": model,
        "input_mode": "evidence_pack" if use_evidence_pack else "full_prefix",
        "ai_rerank_chunks": bool(ai_rerank_chunks and use_evidence_pack),
        "use_vector_search": bool(use_vector_search and use_evidence_pack),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def outline_cache_matches(meta_path: Path, question: str, model: str) -> bool:
    """Verify if the cached outline matches current parameters."""
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[caching] 缓存元数据读取失败 ({meta_path.name}): {e}",
              file=sys.stderr, flush=True)
        return False
    return (
        meta.get("version") == OUTLINE_CACHE_VERSION
        and meta.get("question") == question
        and meta.get("model") == model
    )


def stable_json_sha256(data) -> str:
    """Calculate a stable hash for json-serializable structures."""
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_step_cache_meta(version: str, question: str, model: str, **dependencies) -> dict:
    """Build standardized cache metadata for a pipeline step."""
    meta = {
        "version": version,
        "question": question,
        "model": model,
    }
    meta.update(dependencies)
    return meta


def step_cache_matches(meta_path: Path, expected_meta: dict) -> bool:
    """Check if all keys in expected metadata match stored cache meta."""
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[caching] 缓存元数据读取失败 ({meta_path.name}): {e}",
              file=sys.stderr, flush=True)
        return False
    return all(meta.get(key) == value for key, value in expected_meta.items())


def load_cached_sections(sections_path: Path, meta_path: Path, expected_meta: dict) -> tuple[list[dict], dict] | None:
    """Load sections and paper reference mapping from cache if valid."""
    if not sections_path.exists() or not step_cache_matches(meta_path, expected_meta):
        return None
    try:
        payload = json.loads(sections_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[caching] 缓存章节数据读取失败 ({sections_path.name}): {e}",
              file=sys.stderr, flush=True)
        return None
    sections = payload.get("sections")
    paper_refs = payload.get("paper_refs")
    if not isinstance(sections, list) or not isinstance(paper_refs, dict):
        return None
    normalized_refs = {}
    for key, value in paper_refs.items():
        try:
            normalized_refs[int(key)] = value
        except (TypeError, ValueError):
            continue
    return sections, normalized_refs


def save_cached_sections(sections_path: Path, meta_path: Path, sections: list[dict],
                         paper_refs: dict, meta: dict) -> None:
    """Save section compilation and its dependencies to cache files."""
    sections_path.write_text(json.dumps({
        "sections": sections,
        "paper_refs": paper_refs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cached_report(report_path: Path, meta_path: Path, expected_meta: dict) -> str | None:
    """Load assembled report Markdown if cache matches."""
    if not report_path.exists() or not step_cache_matches(meta_path, expected_meta):
        return None
    try:
        return report_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[caching] 缓存报告读取失败 ({report_path.name}): {e}",
              file=sys.stderr, flush=True)
        return None


def save_cached_report(report_path: Path, meta_path: Path, report: str, meta: dict) -> None:
    """Save assembled report markdown and its metadata cache."""
    report_path.write_text(report, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
