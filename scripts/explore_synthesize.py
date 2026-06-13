#!/usr/bin/env python3
"""
探索总结工作流：给定研究问题 + Zotero 论文集，直接从论文全文提取答案，
生成结构化报告、叙事文章和示意图。

7 步流水线 + 3 项验证：
  Step 1: 逐篇提取发现 + 动态标签（并发）
  Ver 1: 验证发现是否忠于原文，失败重提取
  Step 2: 根据发现摘要生成报告大纲
  Step 3: 逐节缩略匹配 + 含 cite_key 写作（并行）
  Step 4: 整合为结构化 Markdown 报告 + 矛盾识别
  Ver A: 引用正确性验证
  Ver B: 逻辑一致性验证
  Step 5: 验证通过后改写为叙事文章
  Step 6: （如有验证问题）修正报告后重新生成文章
  Step 7: 生成总结表格 + 示意图
"""

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf
from openai import OpenAI
from zotero_reader import ZoteroReader

FINDINGS_CACHE_VERSION = "2026-06-13-v2"
OUTLINE_CACHE_VERSION = "2026-06-13-v1"


# ── Prompts ────────────────────────────────────────────────────────────────

STEP1_SYSTEM = """你是一位学术研究员。阅读论文全文，判断是否与研究问题相关。

论文信息已在开头给出（作者/年份/题目），请据此在每条 finding 中标注作者的姓和年份（用于引用格式如 "Goldman et al., 2002"）。引用标签 cite_key 从 paper_authors 和 paper_year 提取。

相关性判断标准（宽松——宁可错收，不可遗漏）：
- 论文直接研究该问题 → 相关
- 论文讨论相关机制、综述类似问题、引用了相关研究 → 相关
- 论文涉及相同人群和相同/类似指标（如 EEG、PET、fMRI、CBF）→ 相关
- 论文的结论对该问题有间接启示 → 相关
- 仅当论文完全不涉及研究问题中的核心关键词时才标记不相关

如果相关，提取每条独立发现。tags 的维度由你根据研究问题性质灵活决定。

严格要求:
- quote 必须是论文原文的逐字摘录（英文原句），不得改写、翻译、缩写或合并多个不相邻句子
- claim_cn 只能忠实总结 quote 中明确陈述的内容，不得推论、延伸或添加原文未出现的具体数值
- 如果论文只描述了现象而未讨论代谢/血流机制，只提取现象本身，不要自行添加机制解释

输出 JSON:
{
  "relevant": true/false,
  "findings": [
    {
      "claim_cn": "中文总结（1-2句，具体明确）",
      "quote": "原文证据（原文语言，摘录关键句）",
      "cite_key": "第一作者姓 et al., 年份",
      "tags": {"维度名": "值", ...}
    }
  ]
}
只输出 JSON，不要任何额外文本。"""

STEP2_MODEL_FALLBACK = "deepseek-v4-pro"

STEP2_PROMPT = """你是一位综述作者。以下是从多篇论文中提取的发现摘要。

请据此生成一篇结构化报告的大纲。大纲应根据发现的分布自然形成议题分组。
每个叶子节点需带 search_tags，用于后续筛选匹配的发现。search_tags 的维度应与发现中的 tags 维度对应。

研究问题：{question}

发现列表（含索引）：
{findings}

输出 JSON:
{{
  "title": "报告标题（中文）",
  "sections": [
    {{
      "heading": "一级议题",
      "subsections": [
        {{
          "heading": "子议题",
          "search_tags": {{"维度名": "值或值列表"}}
        }}
      ]
    }}
  ]
}}

要求:
- 大纲层级不超过 3 层（section > subsection 即可）
- search_tags 的值如果是多个候选，用数组表示
- 如果发现明显分为不同人群，应在 outline 中体现
- **重要**: 严格按照研究问题中指定的人群范围组织大纲。如果发现来自无关临床人群（如抑郁症、癫痫、昏迷等），不要为其创建独立章节；若有跨人群对比价值，可归入相关人群章节的附属讨论
- 只输出 JSON"""

STEP3_MATCH_PROMPT = """从以下发现缩略列表中，选出与子议题最相关的发现（3-8条）。

子议题: {heading}
search_tags: {search_tags}

发现缩略：
{findings}

输出 JSON:
{{"matched_indices": [索引号, ...]}}

规则：根据发现的摘要和 tags 与子议题的语义匹配度选择。宽松匹配但只选真正相关的。
只输出 JSON。"""

STEP3_WRITE_PROMPT = """你是一位严谨的综述作者。根据以下发现，撰写报告的一个章节。

章节主题：{heading}
研究问题：{question}

相关发现（`**[ref:N] 作者 et al., 年份**` 标注了编号和引用来源，引用时必须使用 [N] 且作者名与标注一致）：
{findings}

要求:
1. 用报告体撰写，语言为中文
2. 每条核心结论附原文引用（用引号标注 quote）
3. 引用格式: 在句末用 [N] 标注，作者名需与发现中标注的 cite_key 完全一致
4. 如果发现之间存在矛盾，客观陈述，不做强行调和
5. 该节控制在 300-800 字"""

STEP4_PROMPT = """合并以下报告各节为一份完整、风格统一的结构化 Markdown 报告。

要求:
- 保留所有内容和引用（[N] 格式的数字引用）
- 统一各级标题格式
- 修正表述不一致的地方
- 不要删减任何发现
- **重要**: 如果不同章节对同一现象（如丘脑 alpha-代谢耦合方向）存在矛盾发现，必须在新报告中明确指出矛盾所在，并分析可能的原因（如人群差异、实验范式差异、分析方法差异等），不要简单罗列或忽视矛盾
- 如果某些章节涉及研究问题未指定的人群（如抑郁症），将其降级为附录或直接删除

各节内容：
{sections}"""

STEP5_PROMPT = """将以下结构化报告改写为一篇流畅的学术综述文章。

要求:
- 用流畅的叙事语言，非条目式
- 保留所有核心结论和 [N] 格式的数字引用
- **严格保留文末的参考文献列表，一字不改**（包括作者名、标题、期刊、年份，不得改写、缩写或替换）
- 文中引用 [N] 对应的作者名必须与参考文献列表中的作者名完全一致，不得自行推断或编造
- 语言连贯自然，适合直接阅读
- 中文输出

报告：
{report}"""

# ── 验证模块 Prompts ──────────────────────────────────────────────────────

VERIFY_FINDING_PROMPT_OMITTED = True  # Ver 1 改用纯字符串匹配，不再用 LLM

VERIFY_CITATION_PROMPT = """你是学术审稿人。验证报告中每条引用的正确性。

报告文本：
{report}

发现索引（每条发现的正确引用信息）：
{findings_index}

检查每条 [N] 引用:
1. 作者名是否与发现中标注的 cite_key 一致
2. 数值/方向结论是否与发现的 claim_cn 一致
3. 是否存在无引用支持的断言

输出 JSON 数组:
[{{"location": "段落开头文字...", "ref_num": N, "issue": "问题描述", "severity": "error/warning"}}]
只输出 JSON。"""

VERIFY_LOGIC_PROMPT = """你是学术审稿人。检查以下综述报告的逻辑一致性。

报告：
{report}

检查:
1. 不同章节对同一现象的描述是否存在矛盾
2. 结论是否跳跃、过度推断
3. 是否存在关键论点缺乏引用

输出 JSON 数组:
[{{"section": "章节名", "location": "相关段落开头...", "issue": "问题描述", "severity": "error/warning"}}]
只输出 JSON。"""

STEP6_FIX_PROMPT = """你是学术编辑。根据验证反馈修正以下综述报告中的问题。

报告：
{report}

验证反馈（逐条列出的需要修正的问题）：
{verification_feedback}

原始发现索引（每条发现的正确引用信息，供核对）：
{findings_index}

要求:
1. 修正所有标记为 error 的问题（事实错误、引用错误、内容截断）
2. 修正所有标记为 warning 的问题（逻辑不一致、表述不清、引用缺失）
3. 对于相互矛盾的发现（如丘脑 alpha-代谢耦合方向的正/负分歧），补充分析说明，指出可能的原因（如人群差异、实验范式差异、绝对vs归一化功率、样本含疾病状态等）
4. 删除与核心研究人群无关的章节（如抑郁症等，除非它被研究问题明确指定）
5. 保留所有正确的引用和参考文献列表
6. 保持报告体格式和中文输出
7. 输出完整的修正后报告（包含标题、各节正文、参考文献列表）

**严格禁止：**
- 不得在参考文献条目后添加括号注释、补充说明、或"假设为""可能为"等不确定表述
- 不得编造、改写或替换参考文献的作者名、标题、期刊、年份
- 如果无法确认某条引用信息，保留原样不要修改
- 输出必须是纯净的 Markdown 报告，不得包含任何元文本或自我对话"""

STEP7_DIAGRAM_PROMPT = """根据以下综述报告，用 Mermaid flowchart 生成一张总结性示意图。从报告中自动提取：有哪些分组维度（如人群、脑区、指标等），各组内有哪些关键节点，节点之间的关系（正相关/负相关/矛盾）。

报告：
{report}

生成规则：
1. 顶层按报告的自然分组（如人群、实验条件、疾病阶段等）划分子图
2. 每个子图内列出该组的关键发现节点，简洁中文，每条不超过10字
3. 节点关系用文字标注（如"正相关""负相关""双向"），不要用 [+][-] 等符号
4. 不同方向用 classDef 着色：green（正相关）、red（负相关）、orange（双向/矛盾）
5. 节点定义只能用 A[标签] 格式，方括号内禁止再嵌套方括号
6. 只输出 ```mermaid 代码块，无其他文字

**Mermaid class 语法（严格遵守）：**
正确：class A,B,C green
错误：class A,B,C,green（类名不能加逗号前缀）
错误：class A,B,C green,red（一行只能一个类名）

输出格式：
```mermaid
flowchart TD
  ...
```"""

STEP7_TABLE_PROMPT = """从以下综述报告中提取关键发现，生成一张总结性 Markdown 表格。

报告：
{report}

要求：
1. 行按频段或指标分组（如 Alpha子频段、BOLD、CBF、葡萄糖代谢等）
2. 列按报告中的人群分组（如 健康年轻、健康衰老、MCI、AD、血管性痴呆 等）
3. 每格用一句话（≤20字）概括耦合方向：↑ 正相关、↓ 负相关、~ 无关联、? 矛盾
4. 表格用标准 Markdown 格式，第一行为列标题
5. 表后附2-3句说明，标注超出表格的重要矛盾或边界条件
6. 只输出表格和说明，不要其他文字"""


# ── Helpers ─────────────────────────────────────────────────────────────────

def extract_pdf_text(pdf_path: Path) -> str:
    doc = pymupdf.open(str(pdf_path))
    text = "\n\n".join(page.get_text() for page in doc if page.get_text().strip())
    doc.close()
    return text


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def findings_cache_key(pdf_path: Path, question: str, model: str) -> str:
    payload = {
        "cache_version": FINDINGS_CACHE_VERSION,
        "pdf_sha256": file_sha256(pdf_path),
        "question": question,
        "model": model,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def chunk_text(text: str, max_chars: int = 10000) -> list[str]:
    """Split text on paragraph boundaries so verification covers long reports."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = []
    current_len = 0
    for para in text.split("\n\n"):
        part_len = len(para) + 2
        if current and current_len + part_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = part_len
        else:
            current.append(para)
            current_len += part_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def call_json(client: OpenAI, system: str, user: str, model: str, max_tokens: int = 4096, retries: int = 2) -> dict:
    """JSON 提取 —— 用 pro 思考模式，靠 prompt 约束格式 + 健壮解析。"""
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
    """无推理 JSON 提取 —— 用于验证等轻量任务，避免 reasoning token 挤占输出。"""
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
    """文本撰写 —— 用 pro 思考模式。默认 temperature=0 保证确定性，叙事写作可调高。"""
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


# ── Step 1: 逐篇提取 ────────────────────────────────────────────────────────

def step1_extract_single(client: OpenAI, pdf_path: Path, meta: dict, question: str, model: str,
                         text_cache_dir: Path, print_lock: threading.Lock,
                         idx: int, total: int, findings_dir: Path = None,
                         force_refresh: bool = False) -> dict:
    stem = pdf_path.stem[:60]
    pdf_hash = file_sha256(pdf_path)
    cache_key = pdf_hash[:16]

    text_cache_path = text_cache_dir / f"{cache_key}.txt"
    if text_cache_path.exists():
        text = text_cache_path.read_text(encoding="utf-8")
        cached = True
    else:
        try:
            text = extract_pdf_text(pdf_path)
            text_cache_dir.mkdir(parents=True, exist_ok=True)
            text_cache_path.write_text(text, encoding="utf-8")
            cached = False
        except Exception as e:
            with print_lock:
                print(f"  [{idx}/{total}] {stem} -> ⚠ PDF提取失败: {e}", flush=True)
            return {"file": pdf_path.name, "relevant": False, "findings": [], "error": str(e),
                    "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
                    "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0)}

    max_chars = 80000
    if len(text) > max_chars:
        text = text[:max_chars]

    fcache_key = findings_cache_key(pdf_path, question, model)
    fcache_path = findings_dir / f"{fcache_key}.json" if findings_dir else None
    if not force_refresh and fcache_path and fcache_path.exists():
        cached_result = json.loads(fcache_path.read_text(encoding="utf-8"))
        relevant = cached_result.get("relevant", False)
        findings = cached_result.get("findings", [])
        with print_lock:
            label = "✓ 缓存" if relevant else "✗ 缓存"
            print(f"  [{idx}/{total}] {stem} -> {label}, {len(findings)}条发现", flush=True)
        return cached_result

    try:
        user_prompt = (
            f"论文信息: {meta.get('authors', '')} ({meta.get('year', '')}). {meta.get('title', '')}\n\n"
            f"研究问题：{question}\n\n论文全文：\n{text}"
        )
        result = call_json(client, STEP1_SYSTEM, user_prompt, model, 16384)
    except Exception as e:
        with print_lock:
            print(f"  [{idx}/{total}] {stem} -> ⚠ API失败: {e}", flush=True)
        return {"file": pdf_path.name, "relevant": False, "findings": [], "error": str(e),
                "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
                "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0)}

    relevant = result.get("relevant", False)
    findings = result.get("findings", [])
    source = "缓存" if cached else "提取"
    charset = len(text)

    with print_lock:
        if relevant:
            print(f"  [{idx}/{total}] {stem} -> ✓ 相关, {len(findings)}条发现 ({source}, {charset}字)", flush=True)
            for j, f in enumerate(findings[:3]):
                print(f"       #{j+1} {f.get('claim_cn', '?')[:80]}", flush=True)
            if len(findings) > 3:
                print(f"       ... 共{len(findings)}条", flush=True)
        else:
            print(f"  [{idx}/{total}] {stem} -> ✗ 不相关 ({source}, {charset}字)", flush=True)

    result = {"file": pdf_path.name, "relevant": relevant, "findings": findings,
            "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
            "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0),
            "cache": {
                "version": FINDINGS_CACHE_VERSION,
                "key": fcache_key,
                "pdf_sha256": pdf_hash,
                "question": question,
                "model": model,
            }}

    if findings_dir:
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / f"{fcache_key}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def step1_extract_all(client_factory, papers: list[dict], question: str, model: str,
                      text_cache_dir: Path, workers: int, findings_dir: Path) -> list[dict]:
    print(f"\n── Step 1: 逐篇提取 ──", flush=True)
    print_lock = threading.Lock()
    total = len(papers)
    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i, paper in enumerate(papers, 1):
            client = client_factory()
            pdf_path = Path(paper["pdf_path"])
            year = paper.get("date", "").split("-")[0] if paper.get("date") else ""
            meta = {
                "title": paper.get("title", ""),
                "authors": paper.get("authors", ""),
                "year": year,
                "ref_num": i,
            }
            f = executor.submit(step1_extract_single, client, pdf_path, meta, question,
                                model, text_cache_dir, print_lock, i, total, findings_dir)
            futures[f] = pdf_path
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: [Path(p["pdf_path"]).name for p in papers].index(r["file"]))

    relevant_count = sum(1 for r in results if r["relevant"])
    total_findings = sum(len(r.get("findings", [])) for r in results)
    print(f"\n  结果: {relevant_count}篇相关, {total - relevant_count}篇不相关, 共{total_findings}条发现\n", flush=True)
    return results


def load_cached_findings_for_papers(papers: list[dict], question: str, model: str,
                                    findings_dir: Path) -> list[dict]:
    """Load only exact cache entries for the current paper set/question/model."""
    results = []
    missing = []
    for i, paper in enumerate(papers, 1):
        pdf_path = Path(paper["pdf_path"])
        key = findings_cache_key(pdf_path, question, model)
        path = findings_dir / f"{key}.json"
        if not path.exists():
            missing.append(pdf_path.name)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        cache_meta = data.get("cache", {})
        if (
            cache_meta.get("version") != FINDINGS_CACHE_VERSION
            or cache_meta.get("question") != question
            or cache_meta.get("model") != model
            or cache_meta.get("pdf_sha256") != file_sha256(pdf_path)
        ):
            missing.append(pdf_path.name)
            continue
        data["ref_num"] = i
        results.append(data)

    if missing:
        preview = ", ".join(missing[:5])
        more = f" 等 {len(missing)} 篇" if len(missing) > 5 else ""
        raise RuntimeError(f"缺少当前问题/模型对应的 findings 缓存: {preview}{more}")
    return results


# ── Step 2: 生成大纲 ─────────────────────────────────────────────────────────

def step2_generate_outline(client: OpenAI, all_results: list[dict], question: str,
                           model: str) -> dict:
    print(f"── Step 2: 生成大纲 ──", flush=True)

    relevant_findings_flat = []
    find_idx = 0
    for paper in all_results:
        if not paper["relevant"]:
            continue
        for f in paper.get("findings", []):
            tags_str = ", ".join(f"{k}={v}" for k, v in f.get("tags", {}).items())
            relevant_findings_flat.append(
                f"[{find_idx}] {f.get('claim_cn', '')}  | tags: {tags_str}"
            )
            find_idx += 1

    if not relevant_findings_flat:
        print("  ⚠ 没有相关发现，无法生成大纲", flush=True)
        return {"title": "报告", "sections": []}

    findings_text = "\n".join(relevant_findings_flat)
    try:
        outline = call_json(client, "",
                            STEP2_PROMPT.format(question=question, findings=findings_text),
                            model, 65536)
    except Exception as e:
        print(f"  ⚠ 大纲生成失败: {e}，使用默认大纲", flush=True)
        outline = _fallback_outline(all_results)

    sections = outline.get("sections", [])
    print(f"  📋 {outline.get('title', '报告')}", flush=True)
    _print_outline_tree(sections, indent="    ")
    print(flush=True)
    return outline


def _print_outline_tree(sections: list, indent: str):
    for i, sec in enumerate(sections):
        is_last = i == len(sections) - 1
        prefix = "└─" if is_last else "├─"
        print(f"  {indent}{prefix} {sec.get('heading', '?')}", flush=True)
        subs = sec.get("subsections", [])
        for j, sub in enumerate(subs):
            sub_last = j == len(subs) - 1
            sp = "   " if is_last else "│  "
            sp2 = "└─" if sub_last else "├─"
            print(f"  {indent}{sp}{sp2} {sub.get('heading', '?')}", flush=True)


def _fallback_outline(all_results: list[dict]) -> dict:
    """当 LLM 大纲生成失败时，构建简单的默认大纲。"""
    tag_dims = set()
    for paper in all_results:
        if not paper["relevant"]:
            continue
        for f in paper.get("findings", []):
            tag_dims.update(f.get("tags", {}).keys())

    sections = []

    for dim in ["人群", "population", "Population"]:
        if dim in tag_dims:
            sections.append({
                "heading": "按人群分类",
                "subsections": [
                    {"heading": "正常成年人", "search_tags": {dim: "正常成年人"}},
                    {"heading": "MCI/AD患者", "search_tags": {dim: ["MCI", "AD", "轻度认知障碍", "阿尔茨海默病"]}},
                ]
            })
            break

    for dim in ["指标", "measure", "Measure"]:
        if dim in tag_dims:
            sections.append({
                "heading": "按测量指标分类",
                "subsections": [
                    {"heading": "葡萄糖代谢 (CMRglc)", "search_tags": {dim: ["CMRglc", "葡萄糖代谢", "FDG", "PET"]}},
                    {"heading": "BOLD信号", "search_tags": {dim: ["BOLD", "fMRI"]}},
                    {"heading": "脑血流量 (CBF/灌注)", "search_tags": {dim: ["CBF", "脑血流", "灌注", "ASL"]}},
                ]
            })
            break

    if not sections:
        sections = [{"heading": "所有发现", "subsections": []}]

    return {"title": "报告", "sections": sections}


def outline_cache_matches(meta_path: Path, question: str, model: str) -> bool:
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        meta.get("version") == OUTLINE_CACHE_VERSION
        and meta.get("question") == question
        and meta.get("model") == model
    )


# ── Step 3: 分节合成 ─────────────────────────────────────────────────────────

def step3_match_and_write(client_factory, outline: dict, all_results: list[dict],
                          question: str, model: str, workers: int) -> tuple[list[dict], dict]:
    print(f"── Step 3: 逐节匹配 + 并行写作 ──", flush=True)

    all_findings = []
    fidx = 0
    paper_refs = {}
    for paper in all_results:
        ref_num = paper.get("ref_num", 0)
        if ref_num and ref_num not in paper_refs and paper.get("relevant"):
            paper_refs[ref_num] = {
                "title": paper.get("ref_title", ""),
                "authors": paper.get("ref_authors", ""),
                "year": paper.get("ref_year", ""),
            }
        if not paper["relevant"]:
            continue
        for f in paper.get("findings", []):
            all_findings.append({
                "index": fidx, "file": paper["file"], "ref_num": ref_num,
                "cite_key": f.get("cite_key", ""),
                "claim_cn": f.get("claim_cn", ""), "quote": f.get("quote", ""),
                "tags": f.get("tags", {}),
            })
            fidx += 1

    if not all_findings:
        print("  ⚠ 无 finding 可合成", flush=True)
        return [], {}

    leaves = _collect_leaves(outline.get("sections", []))
    total_leaves = len(leaves)
    if total_leaves == 0:
        print("  ⚠ 大纲无子章节", flush=True)
        return [], {}

    abbreviated = "\n".join(
        f"[{f['index']}] {f['claim_cn']} | tags: {json.dumps(f['tags'], ensure_ascii=False)}"
        for f in all_findings
    )

    def _normalize_indices(indices) -> list[int]:
        seen = set()
        clean = []
        for raw in indices or []:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(all_findings) and idx not in seen:
                clean.append(idx)
                seen.add(idx)
        return clean

    # ── Phase A: 逐节并行匹配 ──
    print(f"    匹配: {total_leaves} 节 × {len(all_findings)}条缩略（并行）", flush=True)
    leaf_matched = {}

    def _match_leaf(pos: int, leaf: dict) -> tuple[int, str, list[int]]:
        client = client_factory()
        heading = leaf["heading"]
        search_tags = leaf.get("search_tags", {})
        try:
            result = call_json(client, "",
                               STEP3_MATCH_PROMPT.format(
                                   heading=heading,
                                   search_tags=json.dumps(search_tags, ensure_ascii=False),
                                   findings=abbreviated),
                               model, 8192)
            indices = result.get("matched_indices", [])
        except Exception as e:
            print(f"    ⚠ {heading} 匹配失败: {e}，取前8条", flush=True)
            indices = list(range(min(8, len(all_findings))))
        indices = _normalize_indices(indices)
        print(f"    {heading}: {len(indices)}条", flush=True)
        return pos, heading, indices

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_match_leaf, pos, leaf): leaf["heading"] for pos, leaf in enumerate(leaves)}
        for future in as_completed(futures):
            pos, heading, indices = future.result()
            leaf_matched[pos] = indices

    # 覆盖率统计（不兜底，未匹配的发现直接丢弃）
    matched_set = set()
    for indices in leaf_matched.values():
        matched_set.update(indices)
    coverage = len(matched_set) / len(all_findings) * 100 if all_findings else 100
    dropped = len(all_findings) - len(matched_set)
    if dropped > 0:
        print(f"    覆盖率: {len(matched_set)}/{len(all_findings)} ({coverage:.0f}%), {dropped}条不相关发现已丢弃", flush=True)
    else:
        print(f"    覆盖率: {len(matched_set)}/{len(all_findings)} ({coverage:.0f}%)", flush=True)

    # ── Phase B: 逐节并行写作 ──
    print(f"    写作: {total_leaves} 节并行", flush=True)

    def _write_leaf(pos: int, leaf: dict) -> tuple[int, dict]:
        client = client_factory()
        heading = leaf["heading"]
        indices = leaf_matched.get(pos, [])
        matched = [all_findings[i] for i in indices if i < len(all_findings)]
        if not matched:
            print(f"    {heading} -> 无发现", flush=True)
            return pos, {"heading": heading, "content": "_该节未匹配到相关发现。_"}

        findings_text = "\n\n".join(
            f"**[ref:{f['ref_num']}] {f['cite_key']}**\n摘要: {f['claim_cn']}\n原文: \"{f['quote']}\""
            for j, f in enumerate(matched)
        )
        try:
            content = call_text(client,
                                STEP3_WRITE_PROMPT.format(heading=heading, question=question, findings=findings_text),
                                model, 32768)
        except Exception as e:
            content = f"撰写失败: {e}"
        print(f"    {heading} -> 完成 ({len(matched)}条)", flush=True)
        return pos, {"heading": heading, "content": content}

    sections_output = [None] * total_leaves
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_write_leaf, pos, leaf): leaf["heading"] for pos, leaf in enumerate(leaves)}
        for future in as_completed(futures):
            pos, section = future.result()
            sections_output[pos] = section

    print(flush=True)
    return [s for s in sections_output if s is not None], paper_refs


def _collect_leaves(sections: list) -> list[dict]:
    leaves = []
    for sec in sections:
        subs = sec.get("subsections", [])
        if subs:
            leaves.extend(_collect_leaves(subs))
        else:
            leaves.append(sec)
    return leaves


def _clean_refs(report: str, paper_refs: dict) -> str:
    """只保留正文中实际引用过的文献，移除孤儿引用。"""
    cited_nums = set(int(m) for m in re.findall(r'\[(\d+)\]', report))
    # 找到参考文献分界线
    ref_marker = "\n## 参考文献\n"
    ref_idx = report.rfind(ref_marker)
    if ref_idx < 0:
        ref_marker = "\n## 参考文献"
        ref_idx = report.rfind(ref_marker)
    if ref_idx < 0:
        return report
    body = report[:ref_idx]
    ref_list_lines = [ref_marker]
    for num in sorted(paper_refs.keys()):
        if num not in cited_nums:
            continue
        info = paper_refs[num]
        ref_list_lines.append(f"[{num}] {info['authors']}. *{info['title']}*. {info['year']}.")
    return body + "\n".join(ref_list_lines)


# ── Step 4: 整合报告 ─────────────────────────────────────────────────────────

def step4_integrate(client: OpenAI, outline: dict, sections: list[dict],
                    question: str, paper_refs: dict, model: str) -> str:
    print(f"── Step 4: 整合报告 ──", flush=True)

    sections_text = "\n\n---\n\n".join(
        f"## {s['heading']}\n\n{s['content']}" for s in sections
    )

    try:
        report = call_text(client,
                           STEP4_PROMPT.format(sections=sections_text),
                           model, 32768)
    except Exception as e:
        print(f"  ⚠ 整合失败: {e}", flush=True)
        report = f"# {outline.get('title', '报告')}\n\n{sections_text}"

    ref_list_lines = ["\n\n---\n\n## 参考文献\n"]
    # 只保留正文中实际引用过的文献
    cited_nums = set(int(m) for m in re.findall(r'\[(\d+)\]', report))
    for num in sorted(paper_refs.keys()):
        if num not in cited_nums:
            continue
        info = paper_refs[num]
        ref_list_lines.append(f"[{num}] {info['authors']}. *{info['title']}*. {info['year']}.")
    ref_list = "\n".join(ref_list_lines)

    report += ref_list
    report = _clean_refs(report, paper_refs)

    print(f"  ✅ 报告生成完成\n", flush=True)
    return report


# ── Step 5: 叙事文章 ─────────────────────────────────────────────────────────

def step5_narrative(client: OpenAI, report: str, model: str) -> str:
    print(f"── Step 5: 叙事文章 ──", flush=True)
    # 保留前 30k 字符正文 + 尾部参考文献（确保引用不丢失）
    max_body = 30000
    if len(report) > max_body:
        ref_marker = "\n## 参考文献\n"
        ref_idx = report.find(ref_marker)
        if ref_idx > 0:
            body = report[:ref_idx]
            refs = report[ref_idx:]
            report_text = body[:max_body] + refs
        else:
            report_text = report[:max_body]
    else:
        report_text = report

    try:
        article = call_text(client,
                            STEP5_PROMPT.format(report=report_text),
                            model, 32768, temperature=0.3)
    except Exception as e:
        print(f"  ⚠ 文章生成失败: {e}", flush=True)
        article = report

    print(f"  ✅ 文章生成完成\n", flush=True)
    return article


# ── Step 7: 生成总结图表 ──────────────────────────────────────────────────────

def step7_summary(client_factory, report: str) -> dict:
    """生成总结表格（优先）和 Mermaid 示意图。两个调用并行。"""
    print(f"── Step 7: 生成总结图表 ──", flush=True)
    max_input = 15000
    report_text = report[:max_input] if len(report) > max_input else report
    result = {"table": "", "diagram": ""}

    def _gen_table():
        c = client_factory()
        try:
            resp = c.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": STEP7_TABLE_PROMPT.format(report=report_text)}],
                temperature=0,
                max_tokens=4096,
                timeout=60,
            )
            t = resp.choices[0].message.content
            if t and "|" in t:
                return t
        except Exception as e:
            print(f"  ⚠ 表格生成失败: {e}", flush=True)
        return ""

    def _gen_diagram():
        c = client_factory()
        try:
            resp = c.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": STEP7_DIAGRAM_PROMPT.format(report=report_text)}],
                temperature=0,
                max_tokens=8192,
                timeout=60,
            )
            d = resp.choices[0].message.content
            if d and "```mermaid" in d:
                return d
        except Exception:
            pass
        return ""

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_table = ex.submit(_gen_table)
        f_diag = ex.submit(_gen_diagram)
        table = f_table.result(timeout=90)
        diagram = f_diag.result(timeout=90)

    if table:
        result["table"] = table
        print(f"  ✅ 表格生成完成", flush=True)
    else:
        print(f"  ⚠ 表格生成失败", flush=True)
    if diagram:
        result["diagram"] = diagram
        print(f"  ✅ 示意图生成完成", flush=True)

    print(flush=True)
    return result


# ── 验证模块 ────────────────────────────────────────────────────────────────

def verify_findings(all_results: list[dict], papers: list[dict],
                    client_factory, extraction_model: str,
                    text_cache_dir: Path, findings_dir: Path,
                    question: str = "", print_lock: threading.Lock = None) -> list[dict]:
    """Ver 1: 用 quote 字符串直接检索原文。找不到则重提取（最多2轮）。"""
    print(f"\n── Ver 1: 验证发现（字符串检索）──", flush=True)
    if print_lock is None:
        print_lock = threading.Lock()

    file_to_pdf = {}
    for p in papers:
        pdf_path = Path(p.get("pdf_path", ""))
        file_to_pdf[pdf_path.name] = pdf_path

    total_findings = 0
    total_failed = 0

    retry_files = set()
    for paper in all_results:
        if not paper.get("relevant") or not paper.get("findings"):
            continue
        file = paper.get("file", "")
        pdf_path = file_to_pdf.get(file)
        if not pdf_path:
            continue

        cache_key = file_sha256(pdf_path)[:16]
        text_path = text_cache_dir / f"{cache_key}.txt"
        if not text_path.exists():
            continue
        full_text = text_path.read_text(encoding="utf-8")

        has_failure = False
        for f in paper["findings"]:
            total_findings += 1
            quote = f.get("quote", "")
            if not quote:
                continue

            # 尝试精确匹配（取前 60/120 字符）
            q60 = quote[:60].strip()
            q120 = quote[:120].strip()

            # 去掉首尾空白和换行后匹配
            clean_text = ' '.join(full_text.split())
            clean_q60 = ' '.join(q60.split())
            clean_q120 = ' '.join(q120.split())

            if clean_q60 in clean_text or clean_q120 in clean_text:
                continue

            # 模糊：去除所有空格和标点匹配
            import re as _re
            stripped_text = _re.sub(r'\s+', '', full_text)
            stripped_q = _re.sub(r'\s+', '', q60)
            if stripped_q and stripped_q in stripped_text:
                continue

            has_failure = True
            total_failed += 1
            print(f"  ❌ {file[:40]}... → quote未在原文找到", flush=True)

        if has_failure:
            retry_files.add(file)

    pass_rate = (total_findings - total_failed) / total_findings * 100 if total_findings else 100
    print(f"  {total_findings - total_failed}/{total_findings} 通过 ({pass_rate:.0f}%)", flush=True)

    if not retry_files:
        print(f"  ✅ 全部通过", flush=True)
        return all_results

    total = len(papers)
    print(f"  🔄 {len(retry_files)} 篇失败，开始重提取（最多2轮）", flush=True)
    for attempt in range(2):
        still_bad = set()
        for i, paper in enumerate(all_results):
            file = paper.get("file", "")
            if file not in retry_files:
                continue
            pdf_path = file_to_pdf.get(file)
            if not pdf_path:
                continue
            meta = {"title": paper.get("ref_title", ""), "authors": paper.get("ref_authors", ""),
                    "year": paper.get("ref_year", ""), "ref_num": paper.get("ref_num", 0)}
            client = client_factory()
            new = step1_extract_single(client, pdf_path, meta, question, extraction_model,
                                       text_cache_dir, print_lock, i + 1, total, findings_dir,
                                       force_refresh=True)
            if new.get("relevant") and new.get("findings"):
                all_results[i] = new
                print(f"  ✅ 重提取成功: {file[:40]}...", flush=True)
            else:
                still_bad.add(file)
                print(f"  ⚠ 重提取仍失败: {file[:40]}...", flush=True)
        if not still_bad:
            break
        retry_files = still_bad

    print(f"  ✅ 验证完毕", flush=True)
    return all_results


def verify_citations(report_text: str, all_results: list[dict], client, model: str) -> str:
    """Ver A: 验证引用正确性。"""
    print(f"\n── Ver A: 引用正确性 ──", flush=True)

    idx_lines = []
    for paper in all_results:
        if not paper.get("relevant"):
            continue
        for f in paper.get("findings", []):
            idx_lines.append(
                f"[ref:{paper.get('ref_num',0)}] {f.get('cite_key','')} | {f.get('claim_cn','')}"
            )
    findings_index = "\n".join(idx_lines)

    issues = []
    for chunk_no, chunk in enumerate(chunk_text(report_text, 10000), 1):
        try:
            result = call_json(client, "", VERIFY_CITATION_PROMPT.format(
                report=f"【报告片段 {chunk_no}】\n{chunk}", findings_index=findings_index), model, 65536)
        except Exception as e:
            return f"⚠ 引用验证失败: {e}"
        chunk_issues = result if isinstance(result, list) else [result]
        for iss in chunk_issues:
            if isinstance(iss, dict):
                iss.setdefault("chunk", chunk_no)
                issues.append(iss)
    if not issues:
        print(f"  ✅ 引用无问题", flush=True)
        return ""

    md = "## 引用正确性检查\n\n"
    for iss in issues:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        md += f"- {icon} **[{iss.get('ref_num','?')}]** {iss.get('location','')[:40]}... → {iss.get('issue','')}\n"
    md += "\n"
    print(f"  {len(issues)} 处问题", flush=True)
    return md


def verify_logic(report_text: str, client, model: str) -> str:
    """Ver B: 验证逻辑一致性。"""
    print(f"\n── Ver B: 逻辑一致性 ──", flush=True)

    issues = []
    for chunk_no, chunk in enumerate(chunk_text(report_text, 10000), 1):
        try:
            result = call_json(client, "", VERIFY_LOGIC_PROMPT.format(
                report=f"【报告片段 {chunk_no}】\n{chunk}"), model, 65536)
        except Exception as e:
            return f"⚠ 逻辑验证失败: {e}"
        chunk_issues = result if isinstance(result, list) else [result]
        for iss in chunk_issues:
            if isinstance(iss, dict):
                iss.setdefault("chunk", chunk_no)
                issues.append(iss)
    if not issues:
        print(f"  ✅ 逻辑无问题", flush=True)
        return ""

    md = "## 逻辑一致性检查\n\n"
    for iss in issues:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        md += f"- {icon} **{iss.get('section','')}** {iss.get('location','')[:40]}... → {iss.get('issue','')}\n"
    md += "\n"
    print(f"  {len(issues)} 处问题", flush=True)
    return md


def step6_fix_report(client: OpenAI, report: str, verification_feedback: str,
                     all_results: list[dict], model: str) -> str:
    """Step 6: 根据验证反馈修正报告。"""
    print(f"\n── Step 6: 修正报告 ──", flush=True)

    idx_lines = []
    for paper in all_results:
        if not paper.get("relevant"):
            continue
        for f in paper.get("findings", []):
            idx_lines.append(
                f"[ref:{paper.get('ref_num',0)}] {f.get('cite_key','')} | claim: {f.get('claim_cn','')} | quote: {f.get('quote','')[:120]}"
            )
    findings_index = "\n".join(idx_lines)

    max_report = 30000
    report_input = report[:max_report] if len(report) > max_report else report

    try:
        fixed = call_text(client,
                          STEP6_FIX_PROMPT.format(
                              report=report_input,
                              verification_feedback=verification_feedback,
                              findings_index=findings_index),
                          model, 65536)
    except Exception as e:
        print(f"  ⚠ 修正失败: {e}，保留原报告", flush=True)
        return report

    print(f"  ✅ 修正完成\n", flush=True)
    return fixed


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="探索总结工作流：给定研究问题和Zotero论文集，生成报告+文章"
    )
    parser.add_argument("collection", nargs="+", help="Zotero 论文集路径（如 '电波 > alpha'，可多个）")
    parser.add_argument("--question", "-q", required=True, help="研究问题")
    parser.add_argument("--output", "-o", default="synthesize_output", help="输出目录")
    parser.add_argument("--model", "-m", default="deepseek-v4-pro", help="模型名")
    parser.add_argument("--workers", "-w", type=int, default=5, help="并发数")
    parser.add_argument("--cache-dir", help="文本缓存目录（默认 output/cache）")
    parser.add_argument("--max-papers", type=int, default=0, help="最大处理论文数（0=无限制）")
    parser.add_argument("--skip-step1", action="store_true", help="跳过Step1，从已有 findings 继续")
    parser.add_argument("--skip-verify", action="store_true", help="跳过所有验证")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")

    api_keys = [api_key]
    for i in range(2, 20):
        k = os.environ.get(f"DEEPSEEK_API_KEY_{i}")
        if k:
            api_keys.append(k)
        else:
            break

    base_url = "https://api.deepseek.com"
    output_dir = Path(args.output).resolve()
    cache_dir = Path(args.cache_dir) if args.cache_dir else output_dir / "cache"
    output_dir.mkdir(parents=True, exist_ok=True)

    _key_cycle = itertools.cycle(api_keys)
    _key_lock = threading.Lock()

    for v in ("all_proxy", "ALL_PROXY", "socks_proxy", "SOCKS_PROXY", "socks5_proxy", "SOCKS5_PROXY"):
        os.environ.pop(v, None)

    def _client_factory():
        with _key_lock:
            key = next(_key_cycle)
        return OpenAI(api_key=key, base_url=base_url)

    client_factory = _client_factory
    client = client_factory()

    # ── 获取论文集 ──
    seen_titles = set()
    all_papers = []
    for col in args.collection:
        print(f"📚 Zotero「{col}」", flush=True)
        with ZoteroReader() as reader:
            papers = reader.get_papers(col)
        n_new = 0
        for p in papers:
            if p["title"] not in seen_titles:
                seen_titles.add(p["title"])
                all_papers.append(p)
                n_new += 1
        print(f"   {len(papers)} 篇 PDF, {n_new} 篇新收录", flush=True)

    papers = all_papers
    if not papers:
        sys.exit("论文集没有可用的 PDF")

    if args.max_papers and args.max_papers < len(papers):
        papers = papers[:args.max_papers]
    print(f"   {len(papers)} 篇 PDF\n", flush=True)

    # ── Step 1 ──
    findings_dir = output_dir / "findings"
    if args.skip_step1:
        print(f"\n── Step 1: 从缓存加载 ──", flush=True)
        if not findings_dir.exists():
            sys.exit("无缓存 findings，请先完整跑一次")
        try:
            all_results = load_cached_findings_for_papers(papers, args.question, args.model, findings_dir)
        except RuntimeError as e:
            sys.exit(str(e))
        relevant_loaded = sum(1 for r in all_results if r.get("relevant"))
        print(f"   加载 {len(all_results)} 篇 findings，其中 {relevant_loaded} 篇相关", flush=True)
    else:
        findings_dir.mkdir(parents=True, exist_ok=True)
        all_results = step1_extract_all(client_factory, papers, args.question,
                                        args.model, cache_dir, args.workers, findings_dir)

    relevant_papers = [r for r in all_results if r["relevant"]]
    if not relevant_papers:
        print("论文集无相关论文。退出。", flush=True)
        return

    # ── Ver 1: 验证发现 ──
    if not args.skip_verify:
        # Ver 1 本地字符串校验；失败时用主模型强制重提取。
        all_results = verify_findings(all_results, papers, client_factory,
                                       args.model,
                                       cache_dir, findings_dir,
                                       args.question)

    # ── Step 2 ──
    outline_path = output_dir / "outline.json"
    outline_meta_path = output_dir / "outline.meta.json"
    if outline_path.exists() and outline_cache_matches(outline_meta_path, args.question, args.model):
        print(f"── Step 2: 加载缓存大纲 ──", flush=True)
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
        print(f"  📋 {outline.get('title', '报告')}", flush=True)
        _print_outline_tree(outline.get("sections", []), indent="    ")
        print(flush=True)
    else:
        outline = step2_generate_outline(client, all_results, args.question, args.model)
        outline_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
        outline_meta_path.write_text(json.dumps({
            "version": OUTLINE_CACHE_VERSION,
            "question": args.question,
            "model": args.model,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Step 3 ──
    sections, paper_refs = step3_match_and_write(client_factory, outline, all_results, args.question, args.model, args.workers)

    if not sections:
        print("撰写失败，无内容产出。退出。", flush=True)
        return

    # ── Step 4 ──
    report = step4_integrate(client, outline, sections, args.question, paper_refs, args.model)

    report_path = output_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"  📄 报告已保存: {report_path}", flush=True)

    # ── Ver A/B: 引用+逻辑验证 ──
    has_issues = False
    verification_report = ""
    if not args.skip_verify:
        va = verify_citations(report, all_results, client, args.model)
        vb = verify_logic(report, client, args.model)
        if va or vb:
            has_issues = True
            verification_report = "\n\n---\n\n# 验证报告\n\n" + va + vb
            verify_path = output_dir / "verification.md"
            verify_path.write_text(verification_report, encoding="utf-8")
            print(f"  📄 验证报告已保存: {verify_path}", flush=True)
        else:
            print(f"  ✅ 验证通过，无问题\n", flush=True)

    # ── Step 6: 修正报告（如验证发现问题）──
    if has_issues:
        print(flush=True)
        report = step6_fix_report(client, report, verification_report, all_results, args.model)
        report = _clean_refs(report, paper_refs)
        report_path = output_dir / "report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"  📄 修正后报告已保存: {report_path}", flush=True)

    # ── Step 5: 叙事文章（基于最终报告）──
    article = step5_narrative(client, report, args.model)

    article_path = output_dir / "article.md"
    article_path.write_text(article, encoding="utf-8")
    print(f"  📄 文章已保存: {article_path}", flush=True)

    # ── Step 7: 总结图表 ──
    summary = step7_summary(client_factory, report)
    if summary["table"]:
        table_path = output_dir / "table.md"
        table_path.write_text(summary["table"], encoding="utf-8")
        print(f"  📄 总结表格已保存: {table_path}", flush=True)
    if summary["diagram"]:
        diagram_path = output_dir / "diagram.md"
        diagram_path.write_text(summary["diagram"], encoding="utf-8")
        print(f"  📄 示意图已保存: {diagram_path}", flush=True)

    (output_dir / "outline.json").write_text(
        json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "outline.meta.json").write_text(json.dumps({
        "version": OUTLINE_CACHE_VERSION,
        "question": args.question,
        "model": args.model,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"全部完成！")
    print(f"  结构化报告: {report_path}")
    print(f"  叙事文章:   {article_path}")
    print(f"  示意图:     {output_dir / 'diagram.md'}")
    print(f"  中间产物:   {findings_dir}/")
    print(f"  大纲:       {output_dir / 'outline.json'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
