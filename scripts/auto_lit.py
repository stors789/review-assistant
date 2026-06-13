#!/usr/bin/env python3
"""
auto_lit: 自然语言 → Semantic Scholar 搜索 → 生成 RIS 文件供 Zotero 导入

用法: python3 scripts/auto_lit.py "<英文关键词>" -o output.ris [-n 20]
"""

import argparse
import fcntl
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
from zotero_reader import ZoteroReader

SS_API = "https://api.semanticscholar.org/graph/v1/paper/search"
SS_KEY = os.environ.get("SS_API_KEY", "")
SS_FIELDS = "title,authors,year,externalIds,journal,publicationDate,abstract,citationCount"

_SS_LOCK_FILE = Path(tempfile.gettempdir()) / "auto_lit_ss_lock.txt"


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
        lines.append(f"KW  - {tag}")
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
        if args.min_citations and cites < args.min_citations:
            print(f"  ⏭ 跳过: {paper.get('title','?')[:60]} ({cites}引用, 低于{args.min_citations})", flush=True)
            skipped += 1
            continue
        entries.append(_to_ris(paper, i, args.tag))

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
