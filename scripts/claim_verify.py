#!/usr/bin/env python3
"""
段落主张拆解 + Zotero 文献原文验证工具。

流程:
  1. 从 Zotero 论文集提取所有 PDF 全文
  2. 用 DeepSeek 将段落拆解为独立学术主张
  3. 用 DeepSeek 为每条主张匹配最相关的论文
  4. 逐一验证每条主张是否得到原文支持
  5. 生成验证报告（JSON + Markdown 摘要）
"""

import argparse
import json
import os
import sys
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf
from openai import OpenAI
from zotero_reader import ZoteroReader

CLAIM_DECOMPOSE_PROMPT = """你是一位严谨的学术编辑。请将以下段落拆解为若干独立且可验证的学术主张，每一条是一个完整的陈述句。

要求：
- 主张必须包含可验证的具体事实或论断，而非泛泛而论
- 不要拆分过细导致含义丢失
- 按原文顺序排列
- 每条主张是自包含的，脱离上下文也能读懂

输出 JSON：
{"claims": ["主张1", "主张2", ...]}
只输出 JSON。"""


MATCH_PAPERS_PROMPT = """你是一位文献检索专家。请判断以下论文列表中哪些与给定主张最相关，适合用于验证该主张。

主张：{claim}

候选论文（标题 | 作者 | 期刊 | 年份）：
{candidates}

选出与主张主题直接相关的论文，最多 {top} 篇。如果没有直接相关论文，返回空列表。
输出 JSON：
{{"relevant_indices": [0, 3, ...]}}
只输出 JSON。"""


CLAIM_VERIFY_PROMPT = """你是一位严谨的学术审稿人。请根据以下论文全文，判断给定主张是否得到原文支持。

论文：
{paper_ref}

待验证主张：
{claim}

请输出 JSON：
{{
  "support": "完全支持 | 部分支持 | 弱支持 | 不支持 | 矛盾",
  "evidence_cn": "摘录原文关键句，附中文翻译",
  "reasoning": "判断理由（50-150字）"
}}
只输出 JSON。"""


SUMMARY_PROMPT = """你是学术审稿人。基于以下逐条验证结果，生成中文总结报告。

验证数据：
{data}

请输出 Markdown 格式报告，包含：
## 1. 总体可靠性评估
## 2. 各主张支持情况汇总（表格）
## 3. 需要修正的主张
## 4. 文献引用建议
## 5. 改进建议"""


def extract_text(pdf_path: Path) -> str:
    doc = pymupdf.open(str(pdf_path))
    pages = [page.get_text() for page in doc if page.get_text().strip()]
    doc.close()
    return "\n\n".join(pages)


def call_json(client: OpenAI, prompt: str, model: str, max_tokens=4096) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是一个输出 JSON 的助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def call_text(client: OpenAI, prompt: str, model: str, max_tokens=4096) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


def decompose_claims(client: OpenAI, paragraph: str, model: str) -> list[str]:
    result = call_json(client, CLAIM_DECOMPOSE_PROMPT + "\n\n段落：\n" + paragraph, model, 2048)
    return result.get("claims", [])


def match_papers(client, claim, papers, model, top_k):
    candidates = "\n".join(
        f"[{i}] {p['title'][:80]} | {p['authors'][:40]} | {p['journal'][:30]} | {p['date']}"
        for i, p in enumerate(papers)
    )
    prompt = MATCH_PAPERS_PROMPT.format(claim=claim, candidates=candidates, top=top_k)
    try:
        result = call_json(client, prompt, model, 1024)
        return result.get("relevant_indices", [])
    except Exception:
        return list(range(min(top_k, len(papers))))  # fallback: first N


def verify_claim(client, claim, paper, model):
    paper_ref = f"{paper['title']}\n作者：{paper['authors']}\n{paper['journal']}, {paper['date']}\nDOI: {paper['doi']}"
    prompt = CLAIM_VERIFY_PROMPT.format(
        claim=claim,
        paper_ref=paper_ref + "\n\n全文：\n" + paper["text"][:25000],
    )
    return call_json(client, prompt, model, 2048)


def main():
    parser = argparse.ArgumentParser(description="段落主张拆解 + Zotero 文献原文验证")
    parser.add_argument("collection", help="Zotero 论文集路径（如 '电波 > alpha'）")
    parser.add_argument("--paragraph", "-p", help="待验证段落文本")
    parser.add_argument("--file", "-f", help="从文件读取段落")
    parser.add_argument("--model", "-m", default="deepseek-v4-flash")
    parser.add_argument("--output", "-o", help="输出 JSON 报告路径")
    parser.add_argument("--top", type=int, default=3, help="每条主张最多验证的论文数")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")

    if args.file:
        paragraph = Path(args.file).read_text(encoding="utf-8")
    elif args.paragraph:
        paragraph = args.paragraph
    else:
        paragraph = sys.stdin.read()

    if not paragraph.strip():
        sys.exit("未提供段落文本")

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    # ── Step 1: 从 Zotero 获取论文集 ──
    print(f"📚 Zotero「{args.collection}」", flush=True)
    with ZoteroReader() as reader:
        papers = reader.get_papers(args.collection)
    if not papers:
        sys.exit(f"论文集没有可用的 PDF")
    print(f"   {len(papers)} 篇 PDF", flush=True)

    # ── Step 2: 拆解主张 ──
    print(f"🔍 拆解段落...", flush=True)
    claims = decompose_claims(client, paragraph, args.model)
    print(f"   {len(claims)} 条主张：", flush=True)
    for i, c in enumerate(claims, 1):
        print(f"   {i}. {c[:80]}{'...' if len(c) > 80 else ''}", flush=True)

    # ── Step 3: 提取论文全文 ──
    print(f"📄 提取全文...", flush=True)
    paper_data = []
    for p in papers:
        try:
            text = extract_text(Path(p["pdf_path"]))
            paper_data.append({**p, "text": text, "text_len": len(text)})
        except Exception as e:
            print(f"   ⚠ 跳过 {p['title'][:50]}: {e}", flush=True)
    print(f"   成功提取 {len(paper_data)} 篇", flush=True)

    # ── Step 4: 逐条匹配+验证 ──
    report = {"collection": args.collection, "paragraph": paragraph, "claims": []}

    for i, claim in enumerate(claims, 1):
        print(f"\n── 主张 {i}/{len(claims)} ──", flush=True)
        print(f"   {claim[:100]}", flush=True)

        # 匹配相关论文
        relevant = match_papers(client, claim, paper_data, args.model, args.top)
        if not relevant:
            print(f"   → 未匹配到相关论文", flush=True)
            report["claims"].append({"claim": claim, "verifications": []})
            continue

        matched = [paper_data[j] for j in relevant if j < len(paper_data)]
        print(f"   匹配 {len(matched)} 篇: {', '.join(p['title'][:30]+'...' for p in matched)}", flush=True)

        claim_verifications = []
        for paper in matched:
            try:
                v = verify_claim(client, claim, paper, args.model)
                v["paper_title"] = paper["title"]
                v["paper_authors"] = paper["authors"]
                v["paper_doi"] = paper["doi"]
                claim_verifications.append(v)
                print(f"     {paper['title'][:35]}... → {v['support']}", flush=True)
            except Exception as e:
                print(f"     ⚠ {paper['title'][:35]}... 失败: {e}", flush=True)

        report["claims"].append({"claim": claim, "verifications": claim_verifications})

    # ── Step 5: 生成总结 ──
    print(f"\n📊 生成总结...", flush=True)
    summary = call_text(client, SUMMARY_PROMPT.format(data=json.dumps(report, ensure_ascii=False)[:12000]), args.model, 4096)

    if args.output:
        report["summary"] = summary
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   报告已保存: {args.output}", flush=True)

    print(f"\n{'='*60}")
    print(summary)
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
