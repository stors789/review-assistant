# evidence_pack.py
"""
EvidencePack system: splitting text, building keyword windows, boundary chunking,
normalizing findings schema, and AI reranking.
"""

import json
import re
import time
from pathlib import Path
from prompts import AI_CHUNK_RERANK_PROMPT
import llm_client

# Constants
SECTION_ALIASES = {
    "introduction": {
        "introduction", "background", "intro", "preamble", "scientific background",
    },
    "abstract": {"abstract", "summary"},
    "methods": {
        "methods", "method", "materials and methods", "materials & methods",
        "experimental procedures", "participants and methods", "subjects and methods",
        "methods and materials",
    },
    "results": {"results", "findings"},
    "discussion": {"discussion", "general discussion"},
    "conclusion": {"conclusion", "conclusions", "concluding remarks"},
    "references": {"references", "bibliography", "literature cited"},
}

SECTION_PRIORITY = {
    "abstract": 5,
    "results": 4,
    "discussion": 4,
    "conclusion": 3,
    "methods": 2,
    "introduction": 2,
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "between", "by", "can",
    "for", "from", "has", "have", "in", "into", "is", "it", "of", "on", "or",
    "that", "the", "their", "these", "this", "to", "was", "were", "with",
    "about", "including", "include", "includes", "among", "across", "within",
    "研究", "比较", "包括", "以及", "情况", "是否", "如何", "关系", "影响",
    "中的", "对于", "进行", "一个", "一种", "人群", "指标", "情况", "总结",
}

RELATION_DIRECTIONS = {
    "increase", "decrease", "positive_association", "negative_association",
    "no_association", "mixed", "not_applicable",
}

VARIABLE_ROLES = {
    "exposure", "outcome", "mediator", "moderator", "descriptor", "unknown",
}

_QUERY_TERMS_CACHE = {}


def _canonical_section(line: str) -> str | None:
    """Return a canonical section name if a line matches database headers."""
    clean = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[IVX]+)\s*[\).\s:-]*", "", line.strip(), flags=re.I)
    clean = re.sub(r"[:.\s]+$", "", clean).lower()
    if not clean or len(clean) > 80:
        return None
    for canonical, names in SECTION_ALIASES.items():
        if clean in names:
            return canonical
    return None


def _looks_like_heading(line: str) -> bool:
    """Check if a line matches heading formats."""
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return False
    if _canonical_section(stripped):
        return True
    if re.match(r"^\s*(?:\d+(?:\.\d+)*|[IVX]+)\s+[\w(]", stripped, re.I):
        return True
    letters = re.sub(r"[^A-Za-z]", "", stripped)
    return bool(letters and len(letters) >= 4 and letters.upper() == letters and len(stripped.split()) <= 8)


def split_text_chunks(text: str, window_chars: int = 6000, overlap_chars: int = 800) -> list[dict]:
    """Split text into detected sections, falling back to sliding windows."""
    lines = text.splitlines()
    headings = []
    offset = 0
    for line in lines:
        line_len = len(line) + 1
        section = _canonical_section(line)
        if section or _looks_like_heading(line):
            headings.append({
                "offset": offset,
                "line": line.strip()[:100],
                "section": section or "unknown",
            })
        offset += line_len

    chunks = []
    useful_headings = [h for h in headings if h["section"] != "unknown"]
    if len(useful_headings) >= 2:
        for i, heading in enumerate(useful_headings):
            start = heading["offset"]
            end = useful_headings[i + 1]["offset"] if i + 1 < len(useful_headings) else len(text)
            chunk = text[start:end].strip()
            if not chunk:
                continue
            chunks.append({
                "chunk_id": f"s{len(chunks):03d}",
                "source": "detected_section",
                "section": heading["section"],
                "heading": heading["line"],
                "char_start": start,
                "char_end": end,
                "text": chunk,
            })

    if not chunks:
        step = max(1, window_chars - overlap_chars)
        for start in range(0, len(text), step):
            end = min(len(text), start + window_chars)
            chunk = text[start:end].strip()
            if not chunk:
                continue
            chunks.append({
                "chunk_id": f"w{len(chunks):03d}",
                "source": "fallback_window",
                "section": "unknown",
                "heading": "",
                "char_start": start,
                "char_end": end,
                "text": chunk,
            })
            if end >= len(text):
                break

    return chunks


def _merge_ranges(ranges: list[tuple[int, int]], max_gap: int = 300) -> list[tuple[int, int]]:
    """Merge overlapping or near-overlapping character ranges."""
    clean = sorted((max(0, start), max(0, end)) for start, end in ranges if end > start)
    merged = []
    for start, end in clean:
        if not merged or start > merged[-1][1] + max_gap:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def build_keyword_windows(text: str, terms: list[str], window_radius: int = 2000) -> list[dict]:
    """Create local windows around query-term hits."""
    if not text or not terms:
        return []

    text_lower = text.lower()
    ranges = []
    for term in terms:
        needle = term.lower()
        if not needle:
            continue
        start = 0
        while True:
            pos = text_lower.find(needle, start)
            if pos == -1:
                break
            ranges.append((max(0, pos - window_radius), min(len(text), pos + len(term) + window_radius)))
            start = pos + max(1, len(needle))

    windows = []
    for idx, (start, end) in enumerate(_merge_ranges(ranges)):
        chunk = text[start:end].strip()
        if not chunk:
            continue
        windows.append({
            "chunk_id": f"k{idx:03d}",
            "source": "keyword_window",
            "section": "keyword_context",
            "heading": "",
            "char_start": start,
            "char_end": end,
            "text": chunk,
        })
    return windows


def build_boundary_chunks(text: str, front_chars: int = 6000, tail_chars: int = 6000) -> list[dict]:
    """Create front matter and tail chunks."""
    chunks = []
    if not text:
        return chunks
    front_end = min(len(text), front_chars)
    front_text = text[:front_end].strip()
    if front_text:
        chunks.append({
            "chunk_id": "front000",
            "source": "front_matter",
            "section": "front_matter",
            "heading": "",
            "char_start": 0,
            "char_end": front_end,
            "text": front_text,
        })
    if len(text) > front_end:
        tail_start = max(front_end, len(text) - tail_chars)
        tail_text = text[tail_start:].strip()
        if tail_text:
            chunks.append({
                "chunk_id": "tail000",
                "source": "tail",
                "section": "tail",
                "heading": "",
                "char_start": tail_start,
                "char_end": len(text),
                "text": tail_text,
            })
    return chunks


def extract_question_terms(question: str, client=None, model: str = None) -> list[str]:
    """Extract search terms using LLM for cross-lingual support or fallback to regex."""
    global _QUERY_TERMS_CACHE
    if question in _QUERY_TERMS_CACHE:
        return _QUERY_TERMS_CACHE[question]

    if client and model:
        print(f"  🔍 正在使用 AI 提取跨语言搜索词...", flush=True)
        prompt = f"""You are an expert academic search query optimizer.
The user wants to find evidence answering the following research question from a set of English PDF papers. 
Research Question: "{question}"

Your task:
1. Extract the core entities and concepts from the question.
2. Translate them into highly relevant English search terms, keywords, and their most common academic synonyms.
3. Return ONLY a JSON object with a single key "search_terms" containing a flat array of strings. Each string should be a 1-3 word noun phrase. Max 30 terms. Do not include stopwords.

Example output:
{{
  "search_terms": ["neural oscillations", "alpha band", "EEG", "cognitive aging", "sleep metabolism"]
}}
"""
        try:
            res = llm_client.call_json(client, "You are a helpful assistant.", prompt, model, 1000)
            terms = res.get("search_terms", [])
            if isinstance(terms, list) and terms:
                valid = [str(t) for t in terms if isinstance(t, str)]
                _QUERY_TERMS_CACHE[question] = valid
                print(f"  [AI 跨语言搜索词] {valid}", flush=True)
                return valid
        except Exception as e:
            print(f"  ⚠ AI关键词提取失败: {e}，回退至基础提取", flush=True)

    raw = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", question)
    _QUERY_TERMS_CACHE[question] = raw
    return raw


def _score_chunk(chunk: dict, terms: list[str]) -> tuple[int, list[str]]:
    """Calculate the relevance score of a chunk."""
    text_lower = chunk["text"].lower()
    hits = []
    score = SECTION_PRIORITY.get(chunk.get("section"), 0)
    for term in terms:
        t = term.lower()
        count = text_lower.count(t)
        if count:
            hits.append(term)
            score += min(count, 5)
    if re.search(r"\b(table|figure|fig\.|表|图)\b", text_lower):
        score += 1
    return score, hits


def _chunk_snippet(text: str, max_chars: int = 500) -> str:
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


def _format_chunk_candidates(chunks: list[dict], max_candidates: int = 80) -> tuple[str, list[str]]:
    ordered = sorted(
        chunks,
        key=lambda c: (
            c.get("source") == "keyword_window",
            c.get("score", 0),
            len(c.get("hits", [])),
        ),
        reverse=True,
    )[:max_candidates]
    ids = [c["chunk_id"] for c in ordered]
    lines = []
    for c in ordered:
        lines.append(
            json.dumps({
                "chunk_id": c["chunk_id"],
                "source": c.get("source", ""),
                "section": c.get("section", ""),
                "heading": c.get("heading", ""),
                "char_start": c.get("char_start", 0),
                "char_end": c.get("char_end", 0),
                "score": c.get("score", 0),
                "hits": c.get("hits", []),
                "snippet": _chunk_snippet(c.get("text", "")),
            }, ensure_ascii=False)
        )
    return "\n".join(lines), ids


def ai_rerank_chunks(client, chunks: list[dict], question: str, model: str,
                     max_chunks: int = 20) -> dict:
    """Rerank candidate chunks using AI."""
    non_reference = [c for c in chunks if c.get("section") != "references"]
    candidates_text, candidate_ids = _format_chunk_candidates(non_reference)
    if not candidates_text:
        return {"enabled": True, "used": False, "reason": "no_candidates", "selected_chunk_ids": []}
    result = llm_client.call_json_light(
        client,
        "",
        AI_CHUNK_RERANK_PROMPT.format(
            question=question,
            candidates=candidates_text,
            max_chunks=max_chunks,
        ),
        model=model,
        max_tokens=4096,
    )
    valid = set(candidate_ids)
    selected = []
    for raw in result.get("selected_chunk_ids", []):
        chunk_id = str(raw).strip()
        if chunk_id in valid and chunk_id not in selected:
            selected.append(chunk_id)
        if len(selected) >= max_chunks:
            break
    return {
        "enabled": True,
        "used": True,
        "candidate_count": len(candidate_ids),
        "selected_count": len(selected),
        "selected_chunk_ids": selected,
        "model": model,
        "rationale": str(result.get("rationale", ""))[:500],
    }


def should_ai_rerank_chunks(chunks: list[dict]) -> tuple[bool, str]:
    """Decide if AI reranking is necessary based on candidates complexity."""
    non_reference = [c for c in chunks if c.get("section") != "references"]
    keyword_windows = [c for c in non_reference if c.get("source") == "keyword_window"]
    max_score = max((c.get("score", 0) for c in non_reference), default=0)
    positive_scored = sum(1 for c in non_reference if c.get("score", 0) > 0)

    if len(non_reference) > 30:
        return True, "many_candidates"
    if len(keyword_windows) > 12:
        return True, "many_keyword_windows"
    if non_reference and max_score <= 2:
        return True, "low_confidence_scores"
    if positive_scored > 20:
        return True, "diffuse_term_hits"
    return False, "programmatic_confident"


def build_evidence_pack(text: str, question: str, max_chars: int = 80000,
                        ai_rerank: bool = False, rerank_client = None,
                        rerank_model: str = "deepseek-v4-flash") -> tuple[str, dict]:
    """Build a bounded, traceable evidence pack."""
    terms = extract_question_terms(question, rerank_client, rerank_model)
    structural_chunks = split_text_chunks(text)
    keyword_windows = build_keyword_windows(text, terms)
    boundary_chunks = build_boundary_chunks(text)
    chunks = boundary_chunks + structural_chunks + keyword_windows
    scored = []
    for chunk in chunks:
        score, hits = _score_chunk(chunk, terms)
        item = dict(chunk)
        item["score"] = score
        item["hits"] = hits
        scored.append(item)

    selected_ids = set()
    non_reference_scored = [c for c in scored if c.get("section") != "references"]
    ai_meta = {"enabled": bool(ai_rerank), "used": False}
    ai_selected_ids = set()

    should_rerank, rerank_reason = should_ai_rerank_chunks(scored)
    ai_meta["trigger"] = rerank_reason

    if ai_rerank and rerank_client and should_rerank:
        try:
            ai_meta = ai_rerank_chunks(rerank_client, scored, question, rerank_model)
            ai_meta["trigger"] = rerank_reason
            ai_selected_ids = set(ai_meta.get("selected_chunk_ids", []))
            for chunk in scored:
                if chunk["chunk_id"] in ai_selected_ids:
                    chunk["score"] += 10
                    chunk["ai_selected"] = True
                else:
                    chunk["ai_selected"] = False
        except Exception as e:
            ai_meta = {"enabled": True, "used": False, "trigger": rerank_reason, "error": str(e)[:500], "selected_chunk_ids": []}

    def add_chunk(chunk: dict):
        selected_ids.add(chunk["chunk_id"])

    base_chunks = non_reference_scored or scored
    for source in ("front_matter", "tail"):
        candidates = [c for c in base_chunks if c.get("source") == source]
        if candidates:
            add_chunk(candidates[0])

    for section in ("abstract", "results", "discussion", "conclusion"):
        candidates = [c for c in non_reference_scored if c.get("section") == section]
        if candidates:
            add_chunk(max(candidates, key=lambda c: c["score"]))

    keyword_scored = [c for c in non_reference_scored if c.get("source") == "keyword_window" and c["hits"]]
    for chunk in sorted(keyword_scored, key=lambda c: c["score"], reverse=True):
        add_chunk(chunk)
        current_len = sum(len(c["text"]) for c in scored if c["chunk_id"] in selected_ids)
        if current_len >= max_chars:
            break

    for chunk in sorted(non_reference_scored or scored, key=lambda c: c["score"], reverse=True):
        if chunk["score"] <= 0 and selected_ids:
            continue
        add_chunk(chunk)
        current_len = sum(len(c["text"]) for c in scored if c["chunk_id"] in selected_ids)
        if current_len >= max_chars:
            break

    selected = [c for c in scored if c["chunk_id"] in selected_ids]
    selected.sort(key=lambda c: c["char_start"])

    parts = []
    sent_chars = 0
    included_chunks = []
    for chunk in selected:
        remaining = max_chars - sent_chars
        if remaining <= 0:
            break
        chunk_text_part = chunk["text"][:remaining]
        sent_chars += len(chunk_text_part)
        included_chunks.append({
            "chunk_id": chunk["chunk_id"],
            "source": chunk["source"],
            "section": chunk["section"],
            "heading": chunk.get("heading", ""),
            "char_start": chunk["char_start"],
            "char_end": min(chunk["char_end"], chunk["char_start"] + len(chunk_text_part)),
            "score": chunk["score"],
            "hits": chunk["hits"],
            "chars_sent": len(chunk_text_part),
        })
        header = (
            f"[chunk {chunk['chunk_id']} | source={chunk['source']} | "
            f"section={chunk['section']} | chars={chunk['char_start']}-{chunk['char_end']} | "
            f"hits={', '.join(chunk['hits'][:8]) or 'none'} | "
            f"ai_selected={str(bool(chunk.get('ai_selected'))).lower()}]"
        )
        parts.append(f"{header}\n{chunk_text_part}")

    # For mapping key consistency, we import or define EVIDENCE_PACK_VERSION
    EVIDENCE_PACK_VERSION = "2026-06-14-v2"
    coverage = {
        "version": EVIDENCE_PACK_VERSION,
        "full_text_chars": len(text),
        "sent_chars": sent_chars,
        "coverage_ratio": round(sent_chars / len(text), 4) if text else 0,
        "total_chunks": len(chunks),
        "selected_chunks": len(included_chunks),
        "question_terms": terms,
        "section_detection": "detected" if any(c["source"] == "detected_section" for c in structural_chunks) else "fallback_window",
        "source_methods": sorted({c["source"] for c in included_chunks}),
        "candidate_source_counts": {
            source: sum(1 for c in chunks if c["source"] == source)
            for source in sorted({c["source"] for c in chunks})
        },
        "ai_rerank": ai_meta,
        "included_chunks": included_chunks,
    }

    pack = (
        "以下是程序从全文构造的 EvidencePack，不一定覆盖全文。"
        "若 EvidencePack 未覆盖全文，不得声称全文没有某发现；只能说在所见片段中未见。\n\n"
        + "\n\n---\n\n".join(parts)
    )
    return pack, coverage


# ── Finding Schema Normalization ──────────────────────────────────────────

def _stringify_metadata_value(value) -> str:
    """Safely convert metadata values to strings."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _normalize_str_dict(value) -> dict:
    """Normalize dictionary values into string values."""
    if not isinstance(value, dict):
        return {}
    return {
        str(k).strip(): _stringify_metadata_value(v)
        for k, v in value.items()
        if str(k).strip() and _stringify_metadata_value(v)
    }


def _normalize_finding_schema(finding: dict) -> dict:
    """Normalize single finding fields to standard schema."""
    legacy_tags = _normalize_str_dict(finding.get("tags"))
    topic_tags = _normalize_str_dict(finding.get("topic_tags"))
    if not topic_tags and legacy_tags:
        topic_tags = dict(legacy_tags)

    raw_relation = finding.get("relation") if isinstance(finding.get("relation"), dict) else {}
    relation = {
        "subject": _stringify_metadata_value(raw_relation.get("subject")),
        "predicate": _stringify_metadata_value(raw_relation.get("predicate")),
        "object": _stringify_metadata_value(raw_relation.get("object")),
        "qualifier": _stringify_metadata_value(raw_relation.get("qualifier")),
        "direction": _stringify_metadata_value(raw_relation.get("direction")).lower(),
    }
    if relation["direction"] not in RELATION_DIRECTIONS:
        relation["direction"] = "not_applicable"

    raw_context = finding.get("context") if isinstance(finding.get("context"), dict) else {}
    context = {
        "study_type": _stringify_metadata_value(raw_context.get("study_type")),
        "sample_or_system": _stringify_metadata_value(raw_context.get("sample_or_system")),
        "condition": _stringify_metadata_value(raw_context.get("condition")),
        "method": _stringify_metadata_value(raw_context.get("method")),
    }

    variables = []
    raw_variables = finding.get("variables") if isinstance(finding.get("variables"), list) else []
    for item in raw_variables:
        if isinstance(item, dict):
            name = _stringify_metadata_value(item.get("name"))
            role = _stringify_metadata_value(item.get("role")).lower()
        else:
            name = _stringify_metadata_value(item)
            role = "unknown"
        if not name:
            continue
        if role not in VARIABLE_ROLES:
            role = "unknown"
        variables.append({"name": name, "role": role})

    constraints = finding.get("constraints") if isinstance(finding.get("constraints"), list) else []
    constraints = [_stringify_metadata_value(c) for c in constraints if _stringify_metadata_value(c)]

    finding["relation"] = relation
    finding["context"] = context
    finding["variables"] = variables
    finding["constraints"] = constraints
    finding["topic_tags"] = topic_tags
    finding["tags"] = topic_tags
    return finding


def format_finding_metadata(finding: dict) -> str:
    """Format finding metadata to string format for prompting."""
    relation = finding.get("relation") or {}
    context = finding.get("context") or {}
    variables = finding.get("variables") or []
    topic_tags = finding.get("topic_tags") or finding.get("tags") or {}

    relation_bits = [
        f"{k}={v}" for k, v in relation.items()
        if v and not (k == "direction" and v == "not_applicable")
    ]
    context_bits = [f"{k}={v}" for k, v in context.items() if v]
    variable_bits = [
        f"{v.get('name')}({v.get('role', 'unknown')})"
        for v in variables if v.get("name")
    ]
    tag_bits = [f"{k}={v}" for k, v in topic_tags.items() if v]

    parts = []
    if relation_bits:
        parts.append("relation: " + "; ".join(relation_bits))
    if context_bits:
        parts.append("context: " + "; ".join(context_bits))
    if variable_bits:
        parts.append("variables: " + ", ".join(variable_bits))
    if tag_bits:
        parts.append("topic_tags: " + "; ".join(tag_bits))
    return " | ".join(parts) if parts else "metadata: none"


def normalize_finding_relevance(result: dict) -> dict:
    """Re-normalize relevance and schema formatting for a full PDF result."""
    findings = result.get("findings") or []
    cleaned = []
    has_usable = False
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        level = str(finding.get("relevance_level", "")).strip().lower()
        if level not in {"direct", "indirect", "background", "irrelevant"}:
            level = "direct" if result.get("relevant", False) else "irrelevant"
        finding["relevance_level"] = level
        finding["include_in_main_report"] = bool(
            finding.get("include_in_main_report", level == "direct")
        ) and level == "direct"
        finding = _normalize_finding_schema(finding)
        if level != "irrelevant":
            has_usable = True
            cleaned.append(finding)
    result["findings"] = cleaned
    result["relevant"] = bool(result.get("relevant", False) or has_usable)
    return result
