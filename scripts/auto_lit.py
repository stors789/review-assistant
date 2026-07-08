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
from config import (
    should_strip_proxy,
    get_zotero_dir,
    get_zotero_api_key,
    get_zotero_library_type,
    get_zotero_library_id,
    get_zotero_web_import,
    get_zotero_sync_timeout,
)
from zotero_web import ZoteroWebClient, ZoteroWebError, wait_for_local_dois

SS_API = "https://api.semanticscholar.org/graph/v1/paper/search"
SS_FIELDS = "title,authors,year,externalIds,journal,publicationDate,abstract,citationCount,openAccessPdf"

PUBMED_SEARCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_API = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


from datetime import datetime

def _get_ss_lock_file() -> Path:
    lock_dir_env = os.environ.get("AUTO_LIT_LOCK_DIR")
    if lock_dir_env:
        return Path(lock_dir_env) / ".auto_lit_ss_lock.txt"
    try:
        home = Path.home()
        if home.is_dir() and os.access(home, os.W_OK):
            return home / ".auto_lit_ss_lock.txt"
    except Exception as e:
        import sys
        print(f"[auto_lit] 临时目录创建失败，回退使用 /tmp: {e}",
              file=sys.stderr, flush=True)
    import tempfile
    return Path(tempfile.gettempdir()) / ".auto_lit_ss_lock.txt"

_SS_LOCK_FILE = _get_ss_lock_file()
CURRENT_YEAR = datetime.now().year


DEFAULT_SCREENING_RULES = {
    "categories": {
        "core": {
            "terms": [
                "rag", "retrieval-augmented generation", "retrieval augmented generation",
                "hybrid search", "dense retrieval", "sparse retrieval", "vector search"
            ],
            "weight": 2,
            "required_for_tier_a": True
        },
        "model": {
            "terms": [
                "llm", "large language model", "large language models", "transformer", "transformers",
                "gpt", "deepseek", "llama", "claude", "foundation model", "foundation models"
            ],
            "weight": 2,
            "required_for_tier_a": True
        },
        "evaluation": {
            "terms": [
                "faithfulness", "hallucination", "hallucinations", "accuracy", "benchmarking",
                "metrics", "evaluation", "evaluating", "human evaluation", "groundedness"
            ],
            "weight": 2,
            "required_for_tier_a": True
        },
        "techniques": {
            "terms": [
                "reranking", "rerank", "query expansion", "document chunking", "vector database"
            ],
            "weight": 1
        },
        "exclusion": {
            "terms": [
                "computer vision", "image generation", "speech recognition", "robotics",
                "reinforcement learning", "rlhf", "hardware", "quantum computing"
            ],
            "weight": -2
        }
    }
}


def _search(query: str, source: str = "ss", limit: int = 20, ss_key: str = "", pubmed_key: str = "") -> list[dict]:
    """搜索文献。支持 Semantic Scholar (ss) 和 PubMed (pubmed)。"""
    if source == "pubmed":
        return _search_pubmed(query, limit, pubmed_key=pubmed_key)
    
    papers = _search_ss(query, limit, ss_key=ss_key)
    if not papers:
        print("  ❌ SS 搜索失败，终止（未切换 OpenAlex）", flush=True)
        sys.exit(1)
    return papers


def _search_pubmed(query: str, limit: int = 20, pubmed_key: str = "") -> list[dict]:
    """从 PubMed 搜索文献并获取详细信息。"""
    import xml.etree.ElementTree as ET
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0",
    }
    if pubmed_key:
        headers["NCBI-API-Key"] = pubmed_key
        
    # Rate limit: with API key we can do 10 req/sec (0.1s delay is safe, we use 0.2s).
    # Without key, limit is 3 req/sec (we use 1.5s delay).
    delay = 0.2 if pubmed_key else 1.5
    
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
        import sys
        print(f"  ❌ PubMed Search API 请求失败: {e}",
              file=sys.stderr, flush=True)
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
        import sys
        print(f"  ❌ PubMed Fetch API (XML) 请求失败: {e}",
              file=sys.stderr, flush=True)
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
                    
                # DOI & PMC ID (for OA PDF link)
                doi = ""
                pmc = ""
                for article_id in article.findall(".//ArticleIdList/ArticleId"):
                    if article_id.attrib.get("IdType") == "doi":
                        doi = article_id.text
                    elif article_id.attrib.get("IdType") == "pmc":
                        pmc = article_id.text
                        
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
                    "citationCount": 0,
                    "oa_pdf_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc}/pdf/" if pmc else None
                })
            except Exception as item_err:
                import sys
                print(f"  ⚠ 解析单篇文献 XML 失败，跳过: {item_err}",
                      file=sys.stderr, flush=True)
    except Exception as e:
        import sys
        print(f"  ❌ 解析 PubMed XML 失败: {e}",
              file=sys.stderr, flush=True)
        return []
        
    return papers


def _search_ss(query: str, limit: int = 20, ss_key: str = "") -> list[dict]:
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
        request_kwargs = {"timeout": 15}
        if ss_key:
            request_kwargs["headers"] = {"x-api-key": ss_key}
        if should_strip_proxy():
            request_kwargs["proxies"] = {"http": None, "https": None}
        r = requests.get(url, **request_kwargs)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        import sys
        print(f"  ⚠ SS: {e}", file=sys.stderr, flush=True)
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
    except Exception as e:
        import sys
        print(f"[auto_lit] Zotero DOI 查询失败: {e}",
              file=sys.stderr, flush=True)
        return set()


def _paper_doi(paper: dict) -> str:
    return (paper.get("externalIds", {}) or {}).get("DOI", "").strip().lower()


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


def screen_paper(paper: dict, min_relevance: int = 4, rules: dict | None = None) -> dict:
    """Rule-based metadata screen for topic relevance before RIS export."""
    if rules is None:
        rules = DEFAULT_SCREENING_RULES

    text = _text_blob(paper)
    citations = paper.get("citationCount", 0) or 0
    year = paper.get("year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None

    score = 0
    reasons = []

    category_hits = {}
    categories = rules.get("categories", {})
    for cat_name, cat_config in categories.items():
        terms = cat_config.get("terms", [])
        weight = cat_config.get("weight", 0)
        
        # Match terms
        hits = _matched_terms(text, set(terms))
        category_hits[cat_name] = hits
        
        if hits:
            score += weight
            if weight < 0:
                reasons.append(f"exclude:{','.join(hits[:3])}")
            elif len(terms) > 20 or cat_name in ("human_study", "coupling"):
                reasons.append(cat_config.get("label", cat_name.replace("_", "-")))
            else:
                reasons.append(f"{cat_name}:{','.join(hits[:3])}")

    penalty, penalty_reason = _citation_penalty(year, citations)
    score += penalty
    if penalty:
        reasons.append(penalty_reason)

    tier_a_required_cats = [
        cat_name for cat_name, cat_config in categories.items()
        if cat_config.get("required_for_tier_a")
    ]
    
    is_tier_a = False
    if tier_a_required_cats:
        is_tier_a = all(category_hits.get(cat) for cat in tier_a_required_cats)

    if is_tier_a:
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


def _candidate_tag_parts(tag: str) -> list[str]:
    return [part.strip() for part in tag.split(";") if part.strip()]


def _filter_candidates(papers: list[dict], args, existing: set[str], rules: dict | None = None) -> tuple[list[dict], int]:
    selected = []
    skipped = 0
    seen_dois = set()
    for paper in papers:
        doi = _paper_doi(paper)
        if doi and (doi in existing or doi in seen_dois):
            print(f"  ⏭ 跳过: {paper.get('title','?')[:70]} (已有/重复)", flush=True)
            skipped += 1
            continue
        if doi:
            seen_dois.add(doi)
        cites = paper.get("citationCount", 0) or 0
        if not args.screen and args.min_citations and cites < args.min_citations:
            print(f"  ⏭ 跳过: {paper.get('title','?')[:60]} ({cites}引用, 低于{args.min_citations})", flush=True)
            skipped += 1
            continue
        tag = args.tag
        if args.screen:
            screen = screen_paper(paper, args.min_relevance, rules)
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
        item = dict(paper)
        item["_zotero_tag"] = tag
        item["_zotero_tags"] = _candidate_tag_parts(tag)
        selected.append(item)
    return selected, skipped


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
    except Exception as e:
        import sys
        print(f"[auto_lit] Zotero 自动导入失败: {e}",
              file=sys.stderr, flush=True)
        return False


def _web_import(args, papers: list[dict], skipped: int = 0) -> bool:
    api_key = args.zotero_api_key or get_zotero_api_key()
    library_type = args.zotero_library_type or get_zotero_library_type()
    library_id = args.zotero_library_id or get_zotero_library_id()
    if not api_key or not library_type or not library_id:
        raise SystemExit("请配置 ZOTERO_API_KEY, ZOTERO_LIBRARY_TYPE, ZOTERO_LIBRARY_ID，或用 CLI 参数指定")
    if not args.collection and not args.collection_key:
        raise SystemExit("Web import 需要 -c/--collection 或 --collection-key 指定目标 Zotero collection")

    client = ZoteroWebClient(api_key=api_key, library_type=library_type, library_id=library_id)
    if args.collection_key:
        collection_key = args.collection_key
        print(f"📁 使用 collection key: {collection_key}", flush=True)
    else:
        print(f"📁 目标 collection: {args.collection}", flush=True)
        collection_key = client.ensure_collection_path(args.collection, create=args.create_collection)
        print(f"   collection key: {collection_key}", flush=True)

    candidates = []
    seen = set()
    for paper in papers:
        doi = _paper_doi(paper)
        if doi and doi in seen:
            print(f"  ⏭ 跳过: {paper.get('title','?')[:70]} (本轮重复)", flush=True)
            skipped += 1
            continue
        seen.add(doi)
        candidates.append(paper)

    web_existing = client.find_existing_dois({_paper_doi(p) for p in candidates})
    new_papers = []
    skipped_web = 0
    for paper in candidates:
        doi = _paper_doi(paper)
        if doi and doi in web_existing:
            print(f"  ⏭ 跳过: {paper.get('title','?')[:70]} (Web 已有)", flush=True)
            skipped_web += 1
            continue
        new_papers.append(paper)

    if not new_papers:
        print("❌ 所有文献已在 Zotero 中", flush=True)
        return False

    result = client.create_items(new_papers, collection_key, _candidate_tag_parts(args.tag))
    ok = len(result.get("successful", {}))
    failed = result.get("failed", {})
    print(f"\n☁️ Zotero Web API 写入: {ok} 篇成功, {len(failed)} 篇失败", flush=True)
    if skipped_web:
        print(f"⏭ Web 去重跳过 {skipped_web} 篇", flush=True)
    if skipped:
        print(f"⏭ 本地/规则筛选跳过 {skipped} 篇", flush=True)
    for idx, err in failed.items():
        title = new_papers[int(idx)].get("title", "?")[:70] if str(idx).isdigit() and int(idx) < len(new_papers) else "?"
        print(f"  ⚠ {title}: {err}", flush=True)

    if ok and args.wait_local_sync:
        dois = {_paper_doi(p) for p in new_papers if _paper_doi(p)}
        print(f"🔄 等待 Zotero Desktop 同步到本地（最多 {args.sync_timeout}s）...", flush=True)
        found, missing = wait_for_local_dois(
            lambda: _get_existing_dois(zotero_dir=args.zotero_dir),
            dois,
            timeout=args.sync_timeout,
            interval=5,
        )
        if missing:
            print(f"  ⚠ 云端已写入，但本地仍未同步 {len(missing)} 个 DOI", flush=True)
        else:
            print(f"  ✅ 本地 Zotero 已同步 {len(found)} 个 DOI", flush=True)
    return bool(ok)


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
    parser.add_argument("--screen-rules", default=None, help="筛选规则 JSON 文件路径")
    parser.add_argument("--min-relevance", type=int, default=4, help="--screen 模式下最低相关性分数")
    parser.add_argument("--ss-api-key", help="Semantic Scholar API Key (或用 SS_API_KEY 环境变量)")
    parser.add_argument("--pubmed-api-key", help="PubMed API Key (或用 PUBMED_API_KEY / NCBI_API_KEY 环境变量)")
    parser.add_argument("--zotero-dir", default=get_zotero_dir(), help="Zotero 数据根目录（优先于环境变量）")
    parser.add_argument("--import-zotero", action="store_true", help="自动打开 Zotero 导入 RIS 文件（仅 macOS）")
    parser.add_argument("--web-import", action="store_true", default=get_zotero_web_import(), help="通过 Zotero Web API 直接入库")
    parser.add_argument("--zotero-api-key", help="Zotero Web API Key (或用 ZOTERO_API_KEY 环境变量)")
    parser.add_argument("--zotero-library-type", choices=["user", "group"], default=get_zotero_library_type(), help="Zotero library 类型")
    parser.add_argument("--zotero-library-id", default=get_zotero_library_id(), help="Zotero userID 或 groupID")
    parser.add_argument("--collection-key", help="直接指定 Zotero collection key，跳过 collection path 解析")
    parser.add_argument("--create-collection", dest="create_collection", action="store_true", default=True, help="自动创建缺失 collection（默认）")
    parser.add_argument("--no-create-collection", dest="create_collection", action="store_false", help="collection 不存在时失败")
    parser.add_argument("--wait-local-sync", action="store_true", default=True, help="Web 入库后等待本地 Zotero 同步（默认）")
    parser.add_argument("--no-wait-local-sync", dest="wait_local_sync", action="store_false", help="Web 入库后不等待本地同步")
    parser.add_argument("--sync-timeout", type=int, default=get_zotero_sync_timeout(), help="等待本地同步秒数")
    args = parser.parse_args()


    ss_key = args.ss_api_key if args.ss_api_key else os.environ.get("SS_API_KEY", "")
    pubmed_key = args.pubmed_api_key if args.pubmed_api_key else os.environ.get("PUBMED_API_KEY", "") or os.environ.get("NCBI_API_KEY", "")

    output = Path(args.output) if args.output else Path(f"lit_{args.tag or uuid.uuid4().hex[:8]}.ris")

    print(f"🔍 搜索 ({args.source}): \"{args.keywords}\"", flush=True)
    papers = _search(args.keywords, args.source, args.limit, ss_key=ss_key, pubmed_key=pubmed_key)
    if not papers:
        print("❌ 未找到匹配文献", flush=True)
        return

    rules = None
    if args.screen_rules:
        import json
        try:
            rules = json.loads(Path(args.screen_rules).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"❌ 无法加载筛选规则 JSON: {e}",
                  file=sys.stderr, flush=True)
            sys.exit(1)

    existing = _get_existing_dois(zotero_dir=args.zotero_dir)
    selected_papers, skipped = _filter_candidates(papers, args, existing, rules)

    if args.web_import:
        try:
            _web_import(args, selected_papers, skipped=skipped)
        except ZoteroWebError as e:
            print(f"❌ Zotero Web API 入库失败: {e}", flush=True)
        return

    entries = []
    for i, paper in enumerate(selected_papers, 1):
        entries.append(_to_ris(paper, i, paper.get("_zotero_tag", args.tag)))

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
