#!/usr/bin/env python3
"""
auto_lit: 自然语言 → Semantic Scholar 搜索 → 生成 RIS 文件供 Zotero 导入

用法: python3 scripts/auto_lit.py "<英文关键词>" -o output.ris [-n 20]
"""

import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
from zotero_reader import ZoteroReader

SS_API = "https://api.semanticscholar.org/graph/v1/paper/search"
SS_KEY = os.environ.get("SS_API_KEY", "")
SS_FIELDS = "title,authors,year,externalIds,journal,publicationDate,abstract,citationCount"

_SS_LOCK_FILE = Path.home() / ".auto_lit_ss_lock.txt"
CURRENT_YEAR = 2026

CORE_TERMS = {
    "theta", "theta-band", "theta band", "theta power", "theta rhythm", "theta rhythms",
    "eeg", "electroencephalography", "electroencephalographic", "qeeg",
}
METABOLIC_TERMS = {
    "fdg", "pet", "glucose", "metabolism", "metabolic", "bold", "fmri",
    "cerebral blood flow", "cbf", "spect", "asl", "perfusion", "neurovascular",
}
POPULATION_TERMS = {
    "healthy", "adult", "adults", "aging", "ageing", "older", "elderly",
    "mci", "mild cognitive impairment", "prodromal", "cognitive decline",
    "alzheimer", "dementia", "lewy", "parkinson", "frontotemporal",
    "neurodegenerative", "normal",
}
HUMAN_STUDY_TERMS = {
    "human", "subjects", "participants", "patients", "adults", "older adults",
}
COUPLING_TERMS = {
    "coupling", "correlat", "association", "relationship", "linked", "mapping",
    "correspondence", "predict", "neurovascular", "electrometabolic",
}
EXCLUSION_TERMS = {
    "mouse", "mice", "rat", "rats", "animal", "pediatric", "paediatric",
    "children", "infant", "newborn", "epilepsy", "seizure", "ketamine",
    "schizophrenia", "bipolar", "depression", "cocaine", "anesthesia",
    "anaesthesia", "olanzapine", "diabetes", "exercise",
}


def _search(query: str, limit: int = 20) -> list[dict]:
    """搜索文献：仅用 SS。失败直接报错，不回退 OpenAlex。"""
    papers = _search_ss(query, limit)
    if not papers:
        print("  ❌ SS 搜索失败，终止（未切换 OpenAlex）", flush=True)
        sys.exit(1)
    return papers


def _search_ss(query: str, limit: int = 20) -> list[dict]:
    # 文件锁限流：确保两次 API 调用间隔 ≥ 1.5 秒，跨进程生效
    _SS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _SS_LOCK_FILE.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        lock.seek(0)
        try:
            last = float((lock.read().strip() or "0"))
        except ValueError:
            last = 0
        gap = 1.5 - (time.time() - last)
        if gap > 0:
            time.sleep(gap)
        lock.seek(0)
        lock.truncate()
        lock.write(str(time.time()))
        lock.flush()

    url = f"{SS_API}?query={quote(query)}&limit={limit}&fields={quote(SS_FIELDS)}"
    try:
        r = requests.get(url, headers={"x-api-key": SS_KEY}, timeout=15,
                         proxies={"http": None, "https": None})  # 绕过代理直连 SS
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"  ⚠ SS: {e}", flush=True)
        return []


def _get_existing_dois() -> set[str]:
    """从 Zotero 数据库获取已有 DOI，用于去重。"""
    try:
        with ZoteroReader() as r:
            rows = r._query(
                "SELECT v.value FROM itemDataValues v "
                "JOIN itemData d ON d.valueID = v.valueID "
                "JOIN fields f ON f.fieldID = d.fieldID "
                "WHERE f.fieldName = 'DOI' AND v.value IS NOT NULL AND v.value != ''"
            )
            return {row[0].strip().lower() for row in rows}
    except Exception:
        return set()


def _text_blob(paper: dict) -> str:
    journal = paper.get("journal", {}) or {}
    jname = journal.get("name", "") if isinstance(journal, dict) else str(journal)
    return " ".join([
        paper.get("title", "") or "",
        paper.get("abstract", "") or "",
        jname or "",
    ]).lower()


def _has_any(text: str, terms: set[str]) -> bool:
    return bool(_matched_terms(text, terms))


def _matched_terms(text: str, terms: set[str]) -> list[str]:
    hits = []
    for term in terms:
        if " " in term or "-" in term:
            matched = term in text
        else:
            matched = re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
        if matched:
            hits.append(term)
    return sorted(hits)


def _citation_penalty(year: int | None, citations: int) -> tuple[int, str]:
    if not year:
        return 0, "year unknown"
    if year >= 2024:
        return 0, "recent paper protected from citation penalty"
    if year >= 2021:
        if citations < 3:
            return -1, f"{citations} citations for {year}"
        return 0, "recent citation count acceptable"
    if citations < 5:
        return -2, f"{citations} citations for older paper"
    if citations < 10:
        return -1, f"{citations} citations for older paper"
    return 0, "citation count acceptable"


def screen_paper(paper: dict, min_relevance: int = 4) -> dict:
    """Rule-based metadata screen for topic relevance before RIS export."""
    text = _text_blob(paper)
    citations = paper.get("citationCount", 0) or 0
    year = paper.get("year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None

    score = 0
    reasons = []

    core_hits = _matched_terms(text, CORE_TERMS)
    if core_hits:
        score += 2
        reasons.append("core:" + ",".join(core_hits[:3]))

    metabolic_hits = _matched_terms(text, METABOLIC_TERMS)
    if metabolic_hits:
        score += 2
        reasons.append("metabolic:" + ",".join(metabolic_hits[:3]))

    population_hits = _matched_terms(text, POPULATION_TERMS)
    if population_hits:
        score += 2
        reasons.append("population:" + ",".join(population_hits[:3]))

    if _has_any(text, HUMAN_STUDY_TERMS):
        score += 1
        reasons.append("human-study")

    if _has_any(text, COUPLING_TERMS):
        score += 1
        reasons.append("coupling-language")

    exclusion_hits = _matched_terms(text, EXCLUSION_TERMS)
    if exclusion_hits:
        score -= 2
        reasons.append("exclude:" + ",".join(exclusion_hits[:3]))

    penalty, penalty_reason = _citation_penalty(year, citations)
    score += penalty
    if penalty:
        reasons.append(penalty_reason)

    if core_hits and metabolic_hits and population_hits:
        tier = "A"
    elif score >= min_relevance:
        tier = "B"
    else:
        tier = "C"

    return {
        "keep": score >= min_relevance,
        "score": score,
        "tier": tier,
        "reasons": reasons,
    }


def _screen_tag(base_tag: str, screen: dict) -> str:
    parts = [part for part in [base_tag, f"screen:{screen['tier']}", f"score:{screen['score']}"] if part]
    return "; ".join(parts)


def _to_ris(paper: dict, idx: int, tag: str = "") -> str:
    """将 SS 论文转为 RIS 条目。"""
    ext = paper.get("externalIds", {})
    doi = ext.get("DOI", "")
    title = paper.get("title", "")
    year = paper.get("year", "")
    journal = paper.get("journal", {}) or {}
    jname = journal.get("name", "") if isinstance(journal, dict) else str(journal)
    vol = journal.get("volume", "") if isinstance(journal, dict) else ""
    pages = journal.get("pages", "") if isinstance(journal, dict) else ""
    abstract = paper.get("abstract", "")

    lines = ["TY  - JOUR"]
    lines.append(f"ID  - ss_{idx}")
    if title:
        lines.append(f"T1  - {title}")
        lines.append(f"TI  - {title}")
    for a in paper.get("authors", []):
        name = a.get("name", "")
        if name:
            lines.append(f"AU  - {name}")
    if year:
        lines.append(f"PY  - {year}")
        lines.append(f"DA  - {year}//")
    if doi:
        lines.append(f"DO  - {doi}")
        lines.append(f"UR  - https://doi.org/{doi}")
    if jname:
        lines.append(f"JO  - {jname}")
        lines.append(f"JF  - {jname}")
    if vol:
        lines.append(f"VL  - {vol}")
    if pages:
        lines.append(f"SP  - {pages}")
    if abstract:
        lines.append(f"AB  - {abstract}")
    if tag:
        for kw in [part.strip() for part in tag.split(";") if part.strip()]:
            lines.append(f"KW  - {kw}")
    lines.append("ER  - ")
    return "\n".join(lines)


def _try_import_ris(ris_path: str) -> bool:
    """用 `open -a Zotero` 触发导入，会弹出导入对话框。"""
    try:
        subprocess.run(["open", "-a", "Zotero", ris_path], check=True, timeout=5)
        print(f"  ✅ 已发送到 Zotero（在导入对话框中确认）", flush=True)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="自动检索文献并生成 RIS 文件")
    parser.add_argument("keywords", help="英文搜索关键词")
    parser.add_argument("-o", "--output", default=None, help="RIS 输出路径（默认自动生成）")
    parser.add_argument("-n", "--limit", type=int, default=20, help="最大返回数")
    parser.add_argument("-m", "--min-citations", type=int, default=0, help="最低引用数（0=不过滤）")
    parser.add_argument("-c", "--collection", default="", help="目标 Zotero 集（提示用）")
    parser.add_argument("-t", "--tag", default="", help="导入后给条目添加的 Zotero 标签")
    parser.add_argument("--screen", action="store_true", help="按标题/摘要做年份感知相关性筛选")
    parser.add_argument("--min-relevance", type=int, default=4, help="--screen 模式下最低相关性分数")
    args = parser.parse_args()

    output = Path(args.output) if args.output else Path(f"ss_{args.tag or uuid.uuid4().hex[:8]}.ris")

    print(f"🔍 搜索: \"{args.keywords}\"", flush=True)
    papers = _search(args.keywords, args.limit)
    if not papers:
        print("❌ 未找到匹配文献", flush=True)
        return

    existing = _get_existing_dois()
    entries = []
    skipped = 0
    for i, paper in enumerate(papers, 1):
        doi = (paper.get("externalIds", {}) or {}).get("DOI", "").strip().lower()
        if doi and doi in existing:
            print(f"  ⏭ 跳过: {paper.get('title','?')[:70]} (已有)", flush=True)
            skipped += 1
            continue
        cites = paper.get("citationCount", 0) or 0
        if not args.screen and args.min_citations and cites < args.min_citations:
            print(f"  ⏭ 跳过: {paper.get('title','?')[:60]} ({cites}引用, 低于{args.min_citations})", flush=True)
            skipped += 1
            continue
        tag = args.tag
        if args.screen:
            screen = screen_paper(paper, args.min_relevance)
            reason = "; ".join(screen["reasons"]) or "no screen signals"
            if not screen["keep"]:
                print(
                    f"  ⏭ 跳过: {paper.get('title','?')[:60]} "
                    f"(screen:{screen['tier']} score={screen['score']}; {reason})",
                    flush=True,
                )
                skipped += 1
                continue
            tag = _screen_tag(args.tag, screen)
            print(
                f"  ✅ 保留: {paper.get('title','?')[:60]} "
                f"(screen:{screen['tier']} score={screen['score']}; {reason})",
                flush=True,
            )
        entries.append(_to_ris(paper, i, tag))

    if not entries:
        print("❌ 所有文献已在 Zotero 中", flush=True)
        return

    output.write_text("\n".join(entries), encoding="utf-8")
    print(f"\n📥 找到 {len(papers)} 篇, {len(entries)} 篇新文献 → {output}", flush=True)
    if skipped:
        print(f"⏭ 跳过 {skipped} 篇（已在 Zotero 中）", flush=True)

    # 自动导入 Zotero
    col_hint = f" → 选「{args.collection}」" if args.collection else ""
    if not _try_import_ris(str(output)):
        print(f"💡 双击 {output} 或用 Zotero → File → Import{col_hint}", flush=True)


if __name__ == "__main__":
    main()
