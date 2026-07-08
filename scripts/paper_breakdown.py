#!/usr/bin/env python3
"""批量拆解 PDF 论文到固定字段，使用 DeepSeek API。"""
import sys
if sys.version_info < (3, 10):
    sys.stderr.write("Error: review-assistant requires Python 3.10 or higher.\n")
    sys.exit(1)

import argparse
import json
import csv
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import extract_text
from errors import PDFExtractionError, LLMCallError
import llm_client
from config import get_api_key, get_base_url, get_model, get_workers, get_zotero_dir, DEFAULT_FLASH_MODEL

FIELDS = {
    "original_title": "原题目",
    "title_cn": "中文题目",
    "authors": "作者",
    "journal_impact": "刊物及影响因子",
    "year": "发表年限",
    "background": "研究背景",
    "objective": "研究目的",
    "methods": "研究方法",
    "results": "研究结果",
    "conclusion": "研究结论",
    "limitations": "不足及展望",
    "innovation": "创新点",
}

SYSTEM_PROMPT = """你是一位资深学术论文审稿人。请仔细阅读以下论文，然后用 JSON 格式提取以下字段：

{
  "original_title": "论文原题目",
  "title_cn": "中文翻译的题目",
  "authors": "作者列表，用分号分隔",
  "journal_impact": "发表刊物名称及影响因子（若能从文中识别），未知则填'未识别'",
  "year": "发表年份",
  "background": "研究背景（200-300字）",
  "objective": "研究目的与问题（100-200字）",
  "methods": "研究方法（200-400字）",
  "results": "研究结果（200-400字）",
  "conclusion": "研究结论（100-300字）",
  "limitations": "不足与展望（100-300字）",
  "innovation": "创新点（100-300字）"
}

要求：
1. 只输出 JSON，不要任何额外文本或解释
2. 字段内容用中文撰写
3. 如果某个信息论文中未提及，填"未提及"
4. 严格使用以上 JSON 结构，不要新增或删减字段"""


# extract_text is now imported from utils


def breakdown_paper(client, text: str, model: str) -> dict:
    """调用 DeepSeek API 拆解论文。"""
    return llm_client.call_json(client, SYSTEM_PROMPT, text, model, max_tokens=4096)


def process_pdfs(pdf_paths: list[Path], output_dir: Path, model: str, api_key: str, base_url: str, workers: int = 1):
    """批量处理 PDF 文件列表（支持并发）。"""
    if not pdf_paths:
        print("未找到可处理的 PDF 文件")
        return

    llm_client.init_client_pool(base_url=base_url, api_key=api_key)

    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(pdf_paths)
    print_lock = threading.Lock()
    completed = 0

    def process_one(pdf_path: Path) -> dict:
        nonlocal completed
        client = llm_client.get_client()
        name = pdf_path.stem
        json_path = output_dir / f"{name}.json"

        if json_path.exists():
            with print_lock:
                completed += 1
                print(f"[{completed}/{total}] {pdf_path.name} -> 已存在，跳过")
            with open(json_path) as f:
                return json.load(f)

        try:
            text = extract_text(pdf_path)
            with print_lock:
                completed += 1
                print(f"[{completed}/{total}] {pdf_path.name} -> 提取 {len(text)} 字符, 调用 {model} ...")

            result = breakdown_paper(client, text, model)
            result["file"] = pdf_path.name

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            with print_lock:
                print(f"  -> 完成: {result.get('original_title', '未知')[:50]}")

            return result

        except PDFExtractionError as e:
            with print_lock:
                print(f"  -> PDF提取失败: {e}")
            return {"file": pdf_path.name, "error": str(e)}
        except LLMCallError as e:
            with print_lock:
                print(f"  -> API调用失败 (attempts={e.attempts}): {e}")
            return {"file": pdf_path.name, "error": str(e)}
        except Exception as e:
            with print_lock:
                print(f"  -> 未知错误: {e}")
            return {"file": pdf_path.name, "error": str(e)}

    clients = workers
    with ThreadPoolExecutor(max_workers=clients) as executor:
        futures = {executor.submit(process_one, p): p for p in pdf_paths}
        all_results = []
        for future in as_completed(futures):
            all_results.append(future.result())

    csv_path = output_dir / "_summary.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["file"] + list(FIELDS.keys()))
        writer.writeheader()
        for r in all_results:
            row = {k: v for k, v in r.items() if k in writer.fieldnames}
            for k in writer.fieldnames:
                row.setdefault(k, "")
            writer.writerow(row)

    print(f"\n全部完成！{total} 篇论文 -> {output_dir}")
    print(f"  JSON: {output_dir}/*.json")
    print(f"  汇总: {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="批量拆解 PDF 论文到固定字段")
    parser.add_argument("--input", "-i", help="PDF 文件夹路径（与 --zotero-collection 二选一）")
    parser.add_argument("--zotero-collection", "-z", help="Zotero 论文集名称（可与 --input 同时用）")
    parser.add_argument("--list-collections", action="store_true", help="列出所有 Zotero 论文集")
    parser.add_argument("--list-papers", help="列出指定 Zotero 论文集的全部文献及 PDF 状态")
    parser.add_argument("--output", "-o", default="output", help="输出文件夹")
    parser.add_argument("--model", "-m", default=get_model(DEFAULT_FLASH_MODEL), help="模型名（默认 deepseek-v4-flash）")
    parser.add_argument("--api-key", "-k", help="DeepSeek API Key（或用 DEEPSEEK_API_KEY 环境变量）")
    parser.add_argument("--base-url", default=get_base_url(), help="API Base URL")
    parser.add_argument("--zotero-dir", default=get_zotero_dir(), help="Zotero 数据根目录（优先于环境变量）")
    parser.add_argument("--workers", "-w", type=int, default=get_workers(3), help="并发数（默认 3）")
    args = parser.parse_args()

    if args.list_collections:
        from zotero_reader import ZoteroReader
        with ZoteroReader(zotero_dir=args.zotero_dir) as reader:
            print(f"{'论文集':<14} {'总数':>5} {'有PDF':>5} {'缺PDF':>5}")
            print("-" * 36)
            for c in reader.list_collections():
                print(f"{c['name']:<14} {c['total']:>5} {c['has_attachment']:>5} {c['missing']:>5}")
        return

    if args.list_papers:
        from zotero_reader import ZoteroReader
        with ZoteroReader(zotero_dir=args.zotero_dir) as reader:
            items = reader.list_items(args.list_papers)
            have = sum(1 for it in items if it["pdf_available"])
            miss = len(items) - have
            print(f"「{args.list_papers}」: {len(items)} 篇文献, {have} 篇有PDF, {miss} 篇缺PDF\n")
            for it in items:
                icon = "[有]" if it["pdf_available"] else "[缺]"
                print(f"{icon} {it['title'][:70]}")
                if it["authors"]:
                    print(f"    {it['authors'][:60]}")
                if it["journal"]:
                    print(f"    {it['journal']} | {it['date']}")
                if it["doi"]:
                    print(f"    DOI: {it['doi']}")
                if it["pdf_available"]:
                    print(f"    {it['pdf_path']}")
                print()
        return

    api_key = args.api_key or get_api_key()
    if not api_key:
        print("请设置 DEEPSEEK_API_KEY 环境变量或用 --api-key 指定")
        sys.exit(1)

    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output).resolve()

    pdf_paths = []
    if args.zotero_collection:
        from zotero_reader import ZoteroReader
        with ZoteroReader(zotero_dir=args.zotero_dir) as reader:
            papers = reader.get_papers(args.zotero_collection)
            for p in papers:
                pdf_paths.append(Path(p["pdf_path"]))
            print(f"Zotero「{args.zotero_collection}」: {len(papers)} 篇")
    if args.input:
        input_dir = Path(args.input).resolve()
        if input_dir.is_dir():
            pdf_paths.extend(sorted(input_dir.glob("*.pdf")))
    if not pdf_paths:
        sys.exit("请用 --input 或 --zotero-collection 指定 PDF 来源")

    process_pdfs(pdf_paths, output_dir, args.model, api_key, base_url, args.workers)


if __name__ == "__main__":
    main()
