#!/usr/bin/env python3
"""
auto_lit: 自然语言 → Semantic Scholar 搜索 → 生成 RIS 文件供 Zotero 导入

用法: python3 scripts/auto_lit.py "<英文关键词>" -o output.ris [-n 20]
"""

import sys
if sys.version_info < (3, 10):
    sys.stderr.write("Error: review-assistant requires Python 3.10 or higher.\n")
    sys.exit(1)

import argparse
try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
from zotero_reader import ZoteroReader
from config import should_strip_proxy, get_zotero_dir

SS_API = "https://api.semanticscholar.org/graph/v1/paper/search"
SS_KEY = os.environ.get("SS_API_KEY", "")
SS_FIELDS = "title,authors,year,externalIds,journal,publicationDate,abstract,citationCount"

PUBMED_SEARCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PUBMED_KEY = os.environ.get("PUBMED_API_KEY", "") or os.environ.get("NCBI_API_KEY", "")

from datetime import datetime

def _get_ss_lock_file() -> Path:
    lock_dir_env = os.environ.get("AUTO_LIT_LOCK_DIR")
    if lock_dir_env:
        return Path(lock_dir_env) / ".auto_lit_ss_lock.txt"
    try:
        home = Path.home()
        if home.is_dir() and os.access(home, os.W_OK):
            return home / ".auto_lit_ss_lock.txt"
    except Exception:
        pass
    import tempfile
    return Path(tempfile.gettempdir()) / ".auto_lit_ss_lock.txt"

_SS_LOCK_FILE = _get_ss_lock_file()
CURRENT_YEAR = datetime.now().year


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


def _search(query: str, source: str = "ss", limit: int = 20) -> list[dict]:
    """搜索文献。支持 Semantic Scholar (ss) 和 PubMed (pubmed)。"""
    if source == "pubmed":
        return _search_pubmed(query, limit)
    
    papers = _search_ss(query, limit)
    if not papers:
        print("  ❌ SS 搜索失败，终止（未切换 OpenAlex）", flush=True)
        sys.exit(1)
    return papers


def _search_pubmed(query: str, limit: int = 20) -> list[dict]:
    """从 PubMed 搜索文献并获取详细信息。"""
    import xml.etree.ElementTree as ET
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0",
    }
    if PUBMED_KEY:
        headers["NCBI-API-Key"] = PUBMED_KEY
        
    # Rate limit: with API key we can do 10 req/sec (0.1s delay is safe, we use 0.2s).
    # Without key, limit is 3 req/sec (we use 1.5s delay).
    delay = 0.2 if PUBMED_KEY else 1.5
    
    # 1. Search PMIDs
    print("  🔍 正在从 PubMed 搜索 PMIDs...", flush=True)
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": limit
    }
    try:
        r = requests.get(PUBMED_SEARCH_API, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ❌ PubMed Search API 请求失败: {e}", flush=True)
        return []
        
    id_list = data.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        print("  ⚠ 未在 PubMed 中找到匹配的 PMIDs", flush=True)
        return []
        
    print(f"  🔍 找到 {len(id_list)} 个 PMIDs，正在获取详细数据 (限流中, 延迟={delay}s)...", flush=True)
    
    # 2. Rate limit
    time.sleep(delay)
    
    # 3. Fetch Details
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(id_list),
        "retmode": "xml"
    }
    try:
        r_fetch = requests.get(PUBMED_FETCH_API, params=fetch_params, headers=headers, timeout=20)
        r_fetch.raise_for_status()
    except Exception as e:
        print(f"  ❌ PubMed Fetch API (XML) 请求失败: {e}", flush=True)
        return []
        
    # 4. Parse XML
    papers = []
    try:
        root = ET.fromstring(r_fetch.content)
        for article in root.findall(".//PubmedArticle"):
            try:
                # PMID
                pmid_el = article.find(".//MedlineCitation/PMID")
                pmid = pmid_el.text if pmid_el is not None else ""
                
                # Title
                title_el = article.find(".//ArticleTitle")
                title = "".join(title_el.itertext()).strip() if title_el is not None else ""
                
                # Authors
                authors = []
                for author in article.findall(".//AuthorList/Author"):
                    last = author.find("LastName")
                    fore = author.find("ForeName")
                    col = author.find("CollectiveName")
                    if last is not None and fore is not None:
                        authors.append(f"{last.text} {fore.text}")
                    elif col is not None:
                        authors.append(col.text)
                    elif last is not None:
                        authors.append(last.text)
                        
                # Journal
                j_title_el = article.find(".//Journal/Title")
                if j_title_el is None or not j_title_el.text:
                    j_title_el = article.find(".//Journal/ISOAbbreviation")
                j_title = j_title_el.text if j_title_el is not None else "Unknown Journal"
                
                # Date / Year
                year = ""
                year_el = article.find(".//JournalIssue/PubDate/Year")
                if year_el is not None and year_el.text:
                    year = year_el.text
                else:
                    medline_date = article.find(".//JournalIssue/PubDate/MedlineDate")
                    if medline_date is not None and medline_date.text:
                        year = medline_date.text.split()[0]
                try:
                    year = int(year)
                except (TypeError, ValueError):
                    year = None
                    
                # DOI
                doi = ""
                for article_id in article.findall(".//ArticleIdList/ArticleId"):
                    if article_id.attrib.get("IdType") == "doi":
                        doi = article_id.text
                        break
                        
                # Abstract
                abstract_texts = []
                for abs_text in article.findall(".//Abstract/AbstractText"):
                    label = abs_text.attrib.get("Label")
                    text = "".join(abs_text.itertext()).strip()
                    if label:
                        abstract_texts.append(f"{label}: {text}")
                    else:
                        abstract_texts.append(text)
                abstract = "\n".join(abstract_texts)
                
                # Build uniform dict
                papers.append({
                    "title": title,
                    "authors": [{"name": a} for a in authors],
                    "year": year,
                    "externalIds": {"DOI": doi or f"PMID:{pmid}"},
                    "journal": {"name": j_title},
                    "abstract": abstract,
                    "citationCount": 0
                })
            except Exception as item_err:
                print(f"  ⚠ 解析单篇文献 XML 失败，跳过: {item_err}", flush=True)
    except Exception as e:
        print(f"  ❌ 解析 PubMed XML 失败: {e}", flush=True)
        return []
        
    return papers


def _search_ss(query: str, limit: int = 20) -> list[dict]:
    # 文件锁限流：确保两次 API 调用间隔 ≥ 1.5 秒，跨进程生效
    _SS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _SS_LOCK_FILE.open("a+", encoding="utf-8") as lock:
        if fcntl:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        elif msvcrt:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)

        try:
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
        finally:
            if fcntl:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            elif msvcrt:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    url = f"{SS_API}?query={quote(query)}&limit={limit}&fields={quote(SS_FIELDS)}"
    try:
        request_kwargs = {"headers": {"x-api-key": SS_KEY}, "timeout": 15}
        if should_strip_proxy():
            request_kwargs["proxies"] = {"http": None, "https": None}
        r = requests.get(url, **request_kwargs)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"  ⚠ SS: {e}", flush=True)
        return []


def _get_existing_dois(zotero_dir=None) -> set[str]:
    """从 Zotero 数据库获取已有 DOI，用于去重。"""
    try:
        with ZoteroReader(zotero_dir=zotero_dir) as r:
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
    """将 SS/PubMed 论文转为 RIS 条目。"""
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
    if doi and not doi.startswith("PMID:"):
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
    import sys
    if sys.platform != "darwin":
        return False
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
    parser.add_argument("-s", "--source", choices=["ss", "pubmed"], default="ss", help="文献检索源，支持 ss (Semantic Scholar) 或 pubmed (PubMed)")
    parser.add_argument("--screen", action="store_true", help="按标题/摘要做年份感知相关性筛选")
    parser.add_argument("--min-relevance", type=int, default=4, help="--screen 模式下最低相关性分数")
    parser.add_argument("--ss-api-key", help="Semantic Scholar API Key (或用 SS_API_KEY 环境变量)")
    parser.add_argument("--pubmed-api-key", help="PubMed API Key (或用 PUBMED_API_KEY / NCBI_API_KEY 环境变量)")
    parser.add_argument("--zotero-dir", default=get_zotero_dir(), help="Zotero 数据根目录（优先于环境变量）")
    parser.add_argument("--import-zotero", action="store_true", help="自动打开 Zotero 导入 RIS 文件（仅 macOS）")
    args = parser.parse_args()


    global SS_KEY, PUBMED_KEY
    if args.ss_api_key:
        SS_KEY = args.ss_api_key
    if args.pubmed_api_key:
        PUBMED_KEY = args.pubmed_api_key

    output = Path(args.output) if args.output else Path(f"lit_{args.tag or uuid.uuid4().hex[:8]}.ris")

    print(f"🔍 搜索 ({args.source}): \"{args.keywords}\"", flush=True)
    papers = _search(args.keywords, args.source, args.limit)
    if not papers:
        print("❌ 未找到匹配文献", flush=True)
        return

    existing = _get_existing_dois(zotero_dir=args.zotero_dir)
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
    if args.import_zotero:
        if not _try_import_ris(str(output)):
            print(f"💡 双击 {output} 或用 Zotero → File → Import{col_hint}", flush=True)
    else:
        print(f"💡 双击 {output} 或用 Zotero → File → Import{col_hint}", flush=True)



if __name__ == "__main__":
    main()
