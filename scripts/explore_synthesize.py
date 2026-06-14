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

FINDINGS_CACHE_VERSION = "2026-06-14-v4"
OUTLINE_CACHE_VERSION = "2026-06-14-v2"
EVIDENCE_PACK_VERSION = "2026-06-14-v2"
STOP_AFTER_CHOICES = ("step1", "ver1", "step2", "step3", "step4")


# ── Prompts ────────────────────────────────────────────────────────────────

STEP1_SYSTEM = """你是一位学术研究员。阅读论文全文，判断是否与研究问题相关。

论文信息已在开头给出（作者/年份/题目），请据此在每条 finding 中标注作者的姓和年份（用于引用格式如 "Goldman et al., 2002"）。引用标签 cite_key 从 paper_authors 和 paper_year 提取。

相关性判断标准（宽松——宁可错收，不可遗漏）：
- 论文直接研究该问题 → 相关
- 论文讨论相关机制、综述类似问题、引用了相关研究 → 相关
- 论文涉及相同研究对象、系统、条件、变量、指标或方法 → 相关
- 论文的结论对该问题有间接启示 → 相关
- 仅当论文完全不涉及研究问题中的核心关键词时才标记不相关

如果相关，提取每条独立发现。每条 finding 都必须标注 finding 与研究问题的关系等级：
- direct: 论文自己的数据/分析直接回答研究问题
- indirect: 论文自己的数据/分析间接支持研究问题，但变量、人群、方法或结论范围不完全一致
- background: 综述性背景、讨论中引用的既往研究、方法学背景、机制解释；只可作为背景，不可作为主结论
- irrelevant: 与研究问题无关，不应输出为 finding

除中文结论和原文证据外，每条 finding 必须提供通用证据关系 schema。
不要硬编码任何领域概念；如果研究问题涉及医学、工程、社会科学或其他领域，都按原文抽取通用 subject/predicate/object、研究情境和变量角色。

严格要求:
- quote 必须是论文原文的逐字摘录（英文原句），不得改写、翻译、缩写或合并多个不相邻句子
- claim_cn 只能忠实总结 quote 中明确陈述的内容，不得推论、延伸或添加原文未出现的具体数值
- 如果论文只描述了现象而未讨论研究问题中的机制或解释，只提取现象本身，不要自行添加机制解释

输出 JSON:
{
  "relevant": true/false,
  "findings": [
    {
      "claim_cn": "中文总结（1-2句，具体明确）",
      "quote": "原文证据（原文语言，摘录关键句）",
      "cite_key": "第一作者姓 et al., 年份",
      "relevance_level": "direct/indirect/background",
      "include_in_main_report": true/false,
      "relation": {
        "subject": "被研究对象/系统/人群/材料/现象",
        "predicate": "关系或作用的动词短语",
        "object": "关联对象/结果/指标/机制",
        "qualifier": "限定条件、范围、强度或不确定性",
        "direction": "increase/decrease/positive_association/negative_association/no_association/mixed/not_applicable"
      },
      "context": {
        "study_type": "研究类型或证据类型",
        "sample_or_system": "样本、人群、系统或材料",
        "condition": "条件、疾病、场景、处理或任务",
        "method": "主要方法、测量或数据来源"
      },
      "variables": [
        {"name": "变量名", "role": "exposure/outcome/mediator/moderator/descriptor/unknown"}
      ],
      "constraints": ["适用范围、边界条件或重要限制"],
      "topic_tags": {"灵活维度名": "灵活值"}
    }
  ]
}
include_in_main_report 仅在 relevance_level 为 direct 时设为 true；indirect/background 设为 false。
topic_tags 只作补充分类；优先把核心证据写入 relation/context/variables。
只输出 JSON，不要任何额外文本。"""

AI_CHUNK_RERANK_PROMPT = """你是一个文献证据包筛选助手。请根据研究问题，从候选文本块中选出最值得纳入 EvidencePack 的 chunk_id。

研究问题：{question}

候选文本块（只有元数据和短片段；你不负责切分全文）：
{candidates}

输出 JSON:
{{
  "selected_chunk_ids": ["chunk_id", "..."],
  "rationale": "一句话说明选择依据"
}}

规则：
- 优先选择直接包含研究对象、变量、方法、结果或结论的文本块
- 不要选择 references/bibliography 类型文本块
- 最多选择 {max_chunks} 个 chunk_id
- 只输出候选列表中实际存在的 chunk_id
- 只输出 JSON，不要额外文本"""

STEP2_MODEL_FALLBACK = "deepseek-v4-pro"

STEP2_PROMPT = """你是一位综述作者。以下是从多篇论文中提取的发现摘要。

请据此生成一篇结构化报告的大纲。大纲应根据发现的关系、研究情境、变量和证据分布自然形成议题分组。
每个叶子节点需带 match_criteria，用于后续筛选匹配的发现。match_criteria 应优先使用 relation/context/variables 字段；topic_tags 只能作为辅助。

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
          "match_criteria": {{
            "relation": {{"subject": "可选", "predicate": "可选", "object": "可选", "direction": "可选"}},
            "context": {{"study_type": "可选", "sample_or_system": "可选", "condition": "可选", "method": "可选"}},
            "variables": ["变量名或变量角色"],
            "topic_tags": {{"维度名": "值或值列表"}}
          }}
        }}
      ]
    }}
  ]
}}

要求:
- 大纲层级不超过 3 层（section > subsection 即可）
- match_criteria 中的值如果是多个候选，用数组表示
- 优先围绕 direct findings 组织主章节；indirect/background 只能作为背景、边界条件或研究缺口，不要把它们提升为主结论
- 如果发现明显分为不同对象、样本、条件、方法、变量或关系类型，应在 outline 中体现
- **重要**: 严格按照研究问题指定的范围组织大纲。来自范围外对象、样本、条件或场景的发现不要创建独立主章节；若有对比价值，可归入边界条件或附属讨论
- 只输出 JSON"""

STEP3_MATCH_PROMPT = """从以下发现缩略列表中，选出与子议题最相关的发现（最多8条）。

子议题: {heading}
match_criteria: {match_criteria}

发现缩略：
{findings}

输出 JSON:
{{"matched_indices": [索引号, ...]}}

规则：根据发现的摘要、relevance_level、relation、context、variables 和 topic_tags 与子议题的语义匹配度选择。优先使用 relation/context/variables 判断；topic_tags 只能辅助。优先选择 direct；只有在没有 direct 时才选择 indirect/background。宽松匹配但只选真正相关的，最多输出8个索引。
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
- 保留与研究问题直接相关的主证据；如果某些内容只是背景、间接证据、方法学说明或来自研究问题未指定的人群，不要写成主结论，可降级为“边界条件/背景/研究缺口”，必要时删除
- **重要**: 如果不同章节对同一对象、变量关系或结论方向存在矛盾发现，必须在新报告中明确指出矛盾所在，并分析可能的原因（如样本差异、条件差异、方法差异、测量口径差异等），不要简单罗列或忽视矛盾
- 如果某些章节涉及研究问题未指定的人群（如抑郁症），只能作为边界条件简短讨论，不能作为主结论；若与研究问题无关则直接删除

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

CLAIM_MAP_EXTRACT_PROMPT = """你是学术审稿人。请从综述报告中抽取核心论断，形成通用 claim map。

报告：
{report}

输出 JSON 数组。每个元素格式：
[
  {{
    "claim": "报告中的核心论断，保持原意",
    "scope": "该论断适用的对象、样本、条件、方法或场景",
    "evidence_refs": [1, 2],
    "certainty": "high/medium/low/unclear",
    "location": "该论断所在章节或段落开头"
  }}
]

要求：
- 只抽取对结论有实质作用的论断，不抽取纯背景或参考文献条目
- evidence_refs 只填写报告中明确出现的 [N] 数字引用；没有引用则为空数组
- scope 必须忠实于报告表述，不得自行扩大范围
- 保持领域通用，不使用任何特定学科规则
- 只输出 JSON。"""

CLAIM_MAP_CHECK_PROMPT = """你是学术审稿人。请检查以下 claim map 中的跨论断逻辑问题。

Claim map：
{claim_map}

检查关系类型：
- contradicts: 两条或多条论断互相矛盾但报告未解释
- overgeneralizes: 论断范围比证据或限定条件更宽
- unreferenced: 关键论断没有引用支持
- scope_mismatch: 论断之间比较了不同对象、样本、条件、方法或指标，却写成同一范围
- unsupported_jump: 从证据到结论存在明显跳跃

输出 JSON 数组。每个元素格式：
[
  {{
    "relationship": "contradicts/overgeneralizes/unreferenced/scope_mismatch/unsupported_jump",
    "claim_indices": [0, 2],
    "location": "相关章节或段落开头",
    "issue": "问题描述",
    "severity": "error/warning"
  }}
]

要求：
- 如果没有问题，输出 []
- 不要使用任何特定学科规则，只检查通用逻辑、范围和引用支持
- 只输出 JSON。"""

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
3. 对于相互矛盾的发现，补充分析说明，指出可能的原因（如样本差异、条件差异、方法差异、测量口径差异等）
4. 删除与核心研究人群无关的章节（如抑郁症等，除非它被研究问题明确指定）
5. 保留所有正确的引用和参考文献列表
6. 保持报告体格式和中文输出
7. 输出完整的修正后报告（包含标题、各节正文、参考文献列表）

**严格禁止：**
- 不得在参考文献条目后添加括号注释、补充说明、或"假设为""可能为"等不确定表述
- 不得编造、改写或替换参考文献的作者名、标题、期刊、年份
- 如果无法确认某条引用信息，保留原样不要修改
- 输出必须是纯净的 Markdown 报告，不得包含任何元文本或自我对话"""

STEP7_DIAGRAM_PROMPT = """根据以下综述报告，用 Mermaid flowchart 生成一张总结性示意图。从报告中自动提取：有哪些分组维度（如样本、系统、条件、方法、变量或指标等），各组内有哪些关键节点，节点之间的关系（如增加、降低、正相关、负相关、无关联或矛盾）。

报告：
{report}

生成规则：
1. 顶层按报告的自然分组（如样本、系统、条件、方法或证据类型等）划分子图
2. 每个子图内列出该组的关键发现节点，简洁中文，每条不超过10字
3. 节点关系用文字标注（如"增加""降低""正相关""负相关""无关联""矛盾"），不要用 [+][-] 等符号
4. 不同方向用 classDef 着色：green（增加/正相关）、red（降低/负相关）、orange（混合/矛盾/不确定）
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

STEP7_TABLE_VIEW_PROMPT = """请根据以下综述报告，提出 2-3 个可用于总结直接证据的 Markdown 表格视图。

报告：
{report}

输出 JSON 数组：
[
  {{
    "title": "表格标题",
    "row_dimension": "行维度，如对象/变量/条件/方法/结果类别",
    "column_dimension": "列维度，如样本/系统/场景/证据类型/比较组",
    "cell_schema": "每格内容格式，例如：主要发现；证据强度；引用",
    "coverage_rationale": "为什么这个视图能覆盖直接证据",
    "estimated_direct_evidence_coverage": 0.0
  }}
]

要求：
- 保持领域通用，不预设特定学科的行列维度
- 优先覆盖报告中的直接证据和核心引用
- 不强制使用方向箭头；只有主题明确涉及方向性关系时才建议使用
- estimated_direct_evidence_coverage 用 0 到 1 的数字估计
- 只输出 JSON。"""

STEP7_TABLE_PROMPT = """请根据指定表格视图，从综述报告中生成一张总结性 Markdown 表格。

报告：
{report}

表格视图：
{table_view}

要求：
1. 表格标题使用视图中的 title
2. 行维度使用 row_dimension，列维度使用 column_dimension
3. 每格遵循 cell_schema，必须包含引用编号（如 [1]）或明确写“无直接证据”
4. 不强制方向箭头；只有报告主题和证据明确涉及方向性关系时才使用 ↑/↓/~/? 等符号
5. 表格用标准 Markdown 格式，第一行为列标题
6. 表后附2-3句说明，标注重要矛盾、边界条件或证据空白
7. 只输出表格和说明，不要其他文字"""


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


def findings_cache_key(pdf_path: Path, question: str, model: str,
                       use_evidence_pack: bool = True,
                       ai_rerank_chunks: bool = False) -> str:
    payload = {
        "cache_version": FINDINGS_CACHE_VERSION,
        "pdf_sha256": file_sha256(pdf_path),
        "question": question,
        "model": model,
        "input_mode": "evidence_pack" if use_evidence_pack else "full_prefix",
        "ai_rerank_chunks": bool(ai_rerank_chunks and use_evidence_pack),
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


# ── EvidencePack: generic text selection for Step 1 ─────────────────────────

SECTION_ALIASES = {
    "abstract": {"abstract", "summary", "synopsis"},
    "introduction": {"introduction", "background"},
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


def _canonical_section(line: str) -> str | None:
    """Return a broad section name if a line looks like a scholarly heading."""
    clean = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[IVX]+)\s*[\).\s:-]*", "", line.strip(), flags=re.I)
    clean = re.sub(r"[:.\s]+$", "", clean).lower()
    if not clean or len(clean) > 80:
        return None
    for canonical, names in SECTION_ALIASES.items():
        if clean in names:
            return canonical
    return None


def _looks_like_heading(line: str) -> bool:
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
    """Split text into detected sections when possible, otherwise sliding windows."""
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
    """Create deduplicated local windows around query-term hits."""
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
    """Create front matter and tail chunks with explicit source labels."""
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

_QUERY_TERMS_CACHE = {}

def extract_question_terms(question: str, client=None, model: str = None) -> list[str]:
    """Extract generic query terms using an LLM to handle cross-lingual translation and synonyms."""
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
            res = call_json(client, "You are a helpful assistant.", prompt, model, 1000)
            terms = res.get("search_terms", [])
            if isinstance(terms, list) and terms:
                valid = [str(t) for t in terms if isinstance(t, str)]
                _QUERY_TERMS_CACHE[question] = valid
                print(f"  [AI 跨语言搜索词] {valid}", flush=True)
                return valid
        except Exception as e:
            print(f"  ⚠ AI关键词提取失败: {e}，回退至基础提取", flush=True)

    # Fallback
    raw = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", question)
    _QUERY_TERMS_CACHE[question] = raw
    return raw


def _score_chunk(chunk: dict, terms: list[str]) -> tuple[int, list[str]]:
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


def ai_rerank_chunks(client: OpenAI, chunks: list[dict], question: str, model: str,
                     max_chunks: int = 20) -> dict:
    """Ask AI to rerank already-built candidate chunks; no base chunking happens here."""
    non_reference = [c for c in chunks if c.get("section") != "references"]
    candidates_text, candidate_ids = _format_chunk_candidates(non_reference)
    if not candidates_text:
        return {"enabled": True, "used": False, "reason": "no_candidates", "selected_chunk_ids": []}
    result = call_json_light(
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
    """Use AI rerank only when programmatic selection is likely under pressure."""
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
    """Build a bounded, traceable evidence pack for a paper."""
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


def _stringify_metadata_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _normalize_str_dict(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        str(k).strip(): _stringify_metadata_value(v)
        for k, v in value.items()
        if str(k).strip() and _stringify_metadata_value(v)
    }


def _normalize_finding_schema(finding: dict) -> dict:
    """Backfill and sanitize the generic finding schema without domain assumptions."""
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
    """Compact, generic metadata summary for outline and matching prompts."""
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
    """Backfill and sanitize finding-level relevance metadata and generic schema."""
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
            cleaned.append(finding)
            has_usable = True
    result["findings"] = cleaned
    result["relevant"] = bool(result.get("relevant", False) or has_usable)
    return result


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
                         force_refresh: bool = False,
                         use_evidence_pack: bool = True,
                         ai_rerank_chunks: bool = False) -> dict:
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
    coverage = None
    if use_evidence_pack:
        prompt_text, coverage = build_evidence_pack(
            text,
            question,
            max_chars=max_chars,
            ai_rerank=ai_rerank_chunks,
            rerank_client=client,
            rerank_model=model,
        )
    else:
        prompt_text = text[:max_chars] if len(text) > max_chars else text

    fcache_key = findings_cache_key(pdf_path, question, model, use_evidence_pack, ai_rerank_chunks)
    fcache_path = findings_dir / f"{fcache_key}.json" if findings_dir else None
    if not force_refresh and fcache_path and fcache_path.exists():
        cached_result = normalize_finding_relevance(json.loads(fcache_path.read_text(encoding="utf-8")))
        relevant = cached_result.get("relevant", False)
        findings = cached_result.get("findings", [])
        with print_lock:
            label = "✓ 缓存" if relevant else "✗ 缓存"
            print(f"  [{idx}/{total}] {stem} -> {label}, {len(findings)}条发现", flush=True)
        return cached_result

    try:
        user_prompt = (
            f"论文信息: {meta.get('authors', '')} ({meta.get('year', '')}). {meta.get('title', '')}\n\n"
            f"研究问题：{question}\n\n论文文本输入：\n{prompt_text}"
        )
        result = call_json(client, STEP1_SYSTEM, user_prompt, model, 16384)
    except Exception as e:
        with print_lock:
            print(f"  [{idx}/{total}] {stem} -> ⚠ API失败: {e}", flush=True)
        return {"file": pdf_path.name, "relevant": False, "findings": [], "error": str(e),
                "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
                "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0)}

    result = normalize_finding_relevance(result)
    relevant = result.get("relevant", False)
    findings = result.get("findings", [])
    source = "缓存" if cached else "提取"
    charset = len(prompt_text)
    input_mode = "EvidencePack+AI重排" if use_evidence_pack and ai_rerank_chunks else ("EvidencePack" if use_evidence_pack else "全文前缀")

    with print_lock:
        if relevant:
            print(f"  [{idx}/{total}] {stem} -> ✓ 相关, {len(findings)}条发现 ({source}, {input_mode}, {charset}字)", flush=True)
            for j, f in enumerate(findings[:3]):
                print(f"       #{j+1} {f.get('claim_cn', '?')[:80]}", flush=True)
            if len(findings) > 3:
                print(f"       ... 共{len(findings)}条", flush=True)
        else:
            print(f"  [{idx}/{total}] {stem} -> ✗ 不相关 ({source}, {input_mode}, {charset}字)", flush=True)

    result = {"file": pdf_path.name, "relevant": relevant, "findings": findings,
            "ref_title": meta.get("title", ""), "ref_authors": meta.get("authors", ""),
            "ref_year": meta.get("year", ""), "ref_num": meta.get("ref_num", 0),
            "cache": {
                "version": FINDINGS_CACHE_VERSION,
                "key": fcache_key,
                "pdf_sha256": pdf_hash,
                "question": question,
                "model": model,
                "input_mode": "evidence_pack" if use_evidence_pack else "full_prefix",
                "ai_rerank_chunks": bool(ai_rerank_chunks and use_evidence_pack),
            }}
    if coverage:
        result["evidence_pack"] = coverage

    if findings_dir:
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / f"{fcache_key}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def step1_extract_all(client_factory, papers: list[dict], question: str, model: str,
                      text_cache_dir: Path, workers: int, findings_dir: Path,
                      use_evidence_pack: bool = True,
                      ai_rerank_chunks: bool = False) -> list[dict]:
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
                                model, text_cache_dir, print_lock, i, total, findings_dir,
                                use_evidence_pack=use_evidence_pack,
                                ai_rerank_chunks=ai_rerank_chunks)
            futures[f] = pdf_path
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: [Path(p["pdf_path"]).name for p in papers].index(r["file"]))

    relevant_count = sum(1 for r in results if r["relevant"])
    total_findings = sum(len(r.get("findings", [])) for r in results)
    
    direct_count = 0
    indirect_count = 0
    bg_count = 0
    for r in results:
        for f in r.get("findings", []):
            level = str(f.get("relevance_level", "")).lower()
            if level == "direct":
                direct_count += 1
            elif level == "indirect":
                indirect_count += 1
            elif level == "background":
                bg_count += 1

    print(f"\n  结果: {relevant_count}篇相关, {total - relevant_count}篇不相关, 共{total_findings}条发现", flush=True)
    if total_findings > 0:
        print(f"  明细: direct={direct_count}, indirect={indirect_count}, background={bg_count}\n", flush=True)
    else:
        print()
    return results


def load_cached_findings_for_papers(papers: list[dict], question: str, model: str,
                                    findings_dir: Path, use_evidence_pack: bool = True,
                                    ai_rerank_chunks: bool = False) -> list[dict]:
    """Load only exact cache entries for the current paper set/question/model."""
    results = []
    missing = []
    for i, paper in enumerate(papers, 1):
        pdf_path = Path(paper["pdf_path"])
        key = findings_cache_key(pdf_path, question, model, use_evidence_pack, ai_rerank_chunks)
        path = findings_dir / f"{key}.json"
        if not path.exists():
            missing.append(pdf_path.name)
            continue
        data = normalize_finding_relevance(json.loads(path.read_text(encoding="utf-8")))
        cache_meta = data.get("cache", {})
        if (
            cache_meta.get("version") != FINDINGS_CACHE_VERSION
            or cache_meta.get("question") != question
            or cache_meta.get("model") != model
            or cache_meta.get("pdf_sha256") != file_sha256(pdf_path)
            or cache_meta.get("input_mode") != ("evidence_pack" if use_evidence_pack else "full_prefix")
            or bool(cache_meta.get("ai_rerank_chunks")) != bool(ai_rerank_chunks and use_evidence_pack)
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

    direct_findings_flat = []
    fallback_findings_flat = []
    find_idx = 0
    for paper in all_results:
        if not paper["relevant"]:
            continue
        for f in paper.get("findings", []):
            level = f.get("relevance_level", "direct")
            metadata = format_finding_metadata(f)
            line = f"[{find_idx}] ({level}) {f.get('claim_cn', '')}  | {metadata}"
            if f.get("include_in_main_report", level == "direct"):
                direct_findings_flat.append(line)
            elif level in {"indirect", "background"}:
                fallback_findings_flat.append(line)
            find_idx += 1

    relevant_findings_flat = direct_findings_flat or fallback_findings_flat

    if not relevant_findings_flat:
        print("  ⚠ 没有相关发现，无法生成大纲", flush=True)
        return {"title": "报告", "sections": []}
    if not direct_findings_flat:
        print("  ⚠ 没有 direct findings，使用 indirect/background 生成大纲", flush=True)

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
    dimensions = {
        "subject": {},
        "object": {},
        "sample_or_system": {},
        "condition": {},
        "method": {},
        "direction": {},
        "variable": {},
    }

    def add_value(dim: str, value: str):
        value = str(value or "").strip()
        if not value:
            return
        dimensions.setdefault(dim, {})
        dimensions[dim][value] = dimensions[dim].get(value, 0) + 1

    for paper in all_results:
        if not paper["relevant"]:
            continue
        for f in paper.get("findings", []):
            if not f.get("include_in_main_report", f.get("relevance_level") == "direct"):
                continue
            relation = f.get("relation") or {}
            context = f.get("context") or {}
            add_value("subject", relation.get("subject"))
            add_value("object", relation.get("object"))
            add_value("sample_or_system", context.get("sample_or_system"))
            add_value("condition", context.get("condition"))
            add_value("method", context.get("method"))
            direction = relation.get("direction")
            if direction and direction != "not_applicable":
                add_value("direction", direction)
            for variable in f.get("variables") or []:
                add_value("variable", variable.get("name"))

    sections = []
    labels = {
        "sample_or_system": "按样本或系统分类",
        "condition": "按条件或场景分类",
        "object": "按结果或对象分类",
        "subject": "按研究对象分类",
        "method": "按方法分类",
        "variable": "按变量分类",
        "direction": "按关系方向分类",
    }
    criteria_paths = {
        "sample_or_system": ("context", "sample_or_system"),
        "condition": ("context", "condition"),
        "object": ("relation", "object"),
        "subject": ("relation", "subject"),
        "method": ("context", "method"),
        "direction": ("relation", "direction"),
    }
    for dim in ("sample_or_system", "condition", "object", "subject", "method", "variable", "direction"):
        values = sorted(dimensions.get(dim, {}).items(), key=lambda kv: kv[1], reverse=True)
        values = [value for value, count in values if count > 0][:6]
        if len(values) < 2:
            continue
        subsections = []
        for value in values:
            if dim == "variable":
                criteria = {"variables": [value]}
            else:
                group, key = criteria_paths[dim]
                criteria = {group: {key: value}}
            subsections.append({"heading": value[:60], "match_criteria": criteria})
        sections.append({"heading": labels[dim], "subsections": subsections})
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
            level = f.get("relevance_level", "direct")
            all_findings.append({
                "index": fidx, "file": paper["file"], "ref_num": ref_num,
                "cite_key": f.get("cite_key", ""),
                "claim_cn": f.get("claim_cn", ""), "quote": f.get("quote", ""),
                "relevance_level": level,
                "include_in_main_report": f.get("include_in_main_report", level == "direct"),
                "relation": f.get("relation", {}),
                "context": f.get("context", {}),
                "variables": f.get("variables", []),
                "constraints": f.get("constraints", []),
                "topic_tags": f.get("topic_tags", f.get("tags", {})),
                "tags": f.get("topic_tags", f.get("tags", {})),
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

    main_findings = [f for f in all_findings if f.get("include_in_main_report")]
    if not main_findings:
        main_findings = [f for f in all_findings if f.get("relevance_level") in {"indirect", "background"}]
        if main_findings:
            print("  ⚠ 无 direct findings，Step 3 降级使用 indirect/background", flush=True)
    candidate_indices = {f["index"] for f in main_findings}

    abbreviated = "\n".join(
        f"[{f['index']}] ({f.get('relevance_level','direct')}) {f['claim_cn']} | {format_finding_metadata(f)}"
        for f in main_findings
    )

    def _normalize_indices(indices) -> list[int]:
        seen = set()
        clean = []
        for raw in indices or []:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(all_findings) and idx in candidate_indices and idx not in seen:
                clean.append(idx)
                seen.add(idx)
            if len(clean) >= 8:
                break
        return clean

    # ── Phase A: 逐节并行匹配 ──
    print(f"    匹配: {total_leaves} 节 × {len(main_findings)}条缩略（并行）", flush=True)
    leaf_matched = {}

    def _match_leaf(pos: int, leaf: dict) -> tuple[int, str, list[int]]:
        client = client_factory()
        heading = leaf["heading"]
        match_criteria = leaf.get("match_criteria") or leaf.get("search_tags", {})
        try:
            result = call_json(client, "",
                               STEP3_MATCH_PROMPT.format(
                                   heading=heading,
                                   match_criteria=json.dumps(match_criteria, ensure_ascii=False),
                                   findings=abbreviated),
                               model, 8192)
            indices = result.get("matched_indices", [])
        except Exception as e:
            print(f"    ⚠ {heading} 匹配失败: {e}，取前8条", flush=True)
            indices = [f["index"] for f in main_findings[:8]]
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
    coverage = len(matched_set) / len(candidate_indices) * 100 if candidate_indices else 100
    dropped = len(candidate_indices) - len(matched_set)
    if dropped > 0:
        print(f"    覆盖率: {len(matched_set)}/{len(candidate_indices)} ({coverage:.0f}%), {dropped}条候选发现未使用", flush=True)
    else:
        print(f"    覆盖率: {len(matched_set)}/{len(candidate_indices)} ({coverage:.0f}%)", flush=True)

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
            f"**[ref:{f['ref_num']}] {f['cite_key']}**\n摘要: {f['claim_cn']}\n证据结构: {format_finding_metadata(f)}\n原文: \"{f['quote']}\""
            for j, f in enumerate(matched)
        )
        try:
            content = call_text(client,
                                STEP3_WRITE_PROMPT.format(heading=heading, question=question, findings=findings_text),
                                model, 32768)
        except Exception as e:
            content = f"撰写失败: {e}"

        # --- Citation Sanitizer ---
        # 强制清除该小节中出现但并未分配给该小节的参考文献编号，防止引用漂移
        allowed_refs = {f['ref_num'] for f in matched}
        def _sanitize_refs_block(m):
            inner = m.group(1)
            valid_nums = []
            for part in re.split(r',', inner):
                part = part.strip()
                if '-' in part:
                    try:
                        start, end = map(int, part.split('-'))
                        for n in range(start, end + 1):
                            if n in allowed_refs:
                                valid_nums.append(str(n))
                    except:
                        pass
                else:
                    try:
                        n = int(part)
                        if n in allowed_refs:
                            valid_nums.append(str(n))
                    except:
                        pass
            
            unique_valid = []
            for n in valid_nums:
                if n not in unique_valid:
                    unique_valid.append(n)
                    
            if unique_valid:
                return "[" + ", ".join(unique_valid) + "]"
            return ""

        content = re.sub(r'\[([\d\s,\-]+)\]', _sanitize_refs_block, content)
        # --------------------------

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


def _split_report_refs(report: str) -> tuple[str, str]:
    ref_match = re.search(r"\n##\s*参考文献\b", report)
    if not ref_match:
        return report, ""
    return report[:ref_match.start()], report[ref_match.start():]


def verify_references_programmatic(report: str, paper_refs: dict) -> str:
    """Deterministically check numeric citations and generated reference list."""
    body, refs = _split_report_refs(report)
    body_cites = [int(m) for m in re.findall(r"\[(\d+)\]", body)]
    ref_nums = [int(m) for m in re.findall(r"^\[(\d+)\]\s+", refs, flags=re.MULTILINE)]
    body_set = set(body_cites)
    ref_set = set(ref_nums)
    known_set = set(paper_refs.keys())

    issues = []
    if body_set and not refs:
        issues.append(("error", "正文存在数字引用，但报告缺少参考文献列表。"))

    missing_refs = sorted(body_set - ref_set)
    if missing_refs:
        issues.append(("error", f"正文引用缺少参考文献条目: {missing_refs}"))

    orphan_refs = sorted(ref_set - body_set)
    if orphan_refs:
        issues.append(("warning", f"参考文献列表存在正文未引用条目: {orphan_refs}"))

    unknown_body = sorted(body_set - known_set)
    if unknown_body:
        issues.append(("error", f"正文引用编号不在 paper_refs 中: {unknown_body}"))

    unknown_refs = sorted(ref_set - known_set)
    if unknown_refs:
        issues.append(("error", f"参考文献编号不在 paper_refs 中: {unknown_refs}"))

    duplicate_refs = sorted(n for n in ref_set if ref_nums.count(n) > 1)
    if duplicate_refs:
        issues.append(("error", f"参考文献列表重复编号: {duplicate_refs}"))

    for num in sorted(ref_set & known_set):
        info = paper_refs[num]
        pattern = re.compile(rf"^\[{num}\]\s+(.+)$", flags=re.MULTILINE)
        m = pattern.search(refs)
        if not m:
            continue
        line = m.group(1)
        year = str(info.get("year", "")).strip()
        title = str(info.get("title", "")).strip()
        if year and year not in line:
            issues.append(("warning", f"参考文献[{num}]缺少年份 {year}。"))
        if title and title[:30] not in line:
            issues.append(("warning", f"参考文献[{num}]标题可能与 Zotero 元数据不一致。"))

    if not issues:
        return ""

    md = "## 程序化引用检查\n\n"
    for severity, issue in issues:
        icon = "❌" if severity == "error" else "⚠️"
        md += f"- {icon} {issue}\n"
    md += "\n"
    print(f"  程序化引用检查: {len(issues)} 处问题", flush=True)
    return md


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

def normalize_table_views(raw) -> list[dict]:
    """Normalize proposed Step 7 table views and drop unusable entries."""
    views = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    normalized = []
    for i, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        title = str(view.get("title", "")).strip() or f"总结表 {i + 1}"
        row_dimension = str(view.get("row_dimension", "")).strip()
        column_dimension = str(view.get("column_dimension", "")).strip()
        cell_schema = str(view.get("cell_schema", "")).strip()
        if not row_dimension or not column_dimension:
            continue
        if not cell_schema:
            cell_schema = "主要发现；证据强度；引用"
        try:
            coverage = float(view.get("estimated_direct_evidence_coverage", 0))
        except (TypeError, ValueError):
            coverage = 0
        coverage = max(0.0, min(1.0, coverage))
        normalized.append({
            "title": title[:120],
            "row_dimension": row_dimension[:120],
            "column_dimension": column_dimension[:120],
            "cell_schema": cell_schema[:200],
            "coverage_rationale": str(view.get("coverage_rationale", view.get("why", ""))).strip()[:500],
            "estimated_direct_evidence_coverage": coverage,
        })
    return normalized


def choose_table_view(views: list[dict]) -> dict:
    """Choose the table view with the highest estimated direct evidence coverage."""
    if not views:
        return {
            "title": "直接证据总结表",
            "row_dimension": "核心对象或变量",
            "column_dimension": "样本、条件或证据类型",
            "cell_schema": "主要发现；证据强度；引用",
            "coverage_rationale": "fallback view",
            "estimated_direct_evidence_coverage": 0,
        }
    return max(
        views,
        key=lambda v: (
            v.get("estimated_direct_evidence_coverage", 0),
            bool(v.get("coverage_rationale")),
            len(v.get("row_dimension", "")) + len(v.get("column_dimension", "")),
        ),
    )


def should_stop_after(current_step: str, stop_after: str | None) -> bool:
    return bool(stop_after and current_step == stop_after)


def print_stop_after(current_step: str, output_dir: Path):
    print(f"\n⏹ --stop-after {current_step}: 已停止。输出目录: {output_dir}", flush=True)


def step7_summary(client_factory, report: str) -> dict:
    """生成总结表格（优先）和 Mermaid 示意图。两个调用并行。"""
    print(f"── Step 7: 生成总结图表 ──", flush=True)
    max_input = 15000
    report_text = report[:max_input] if len(report) > max_input else report
    result = {"table": "", "diagram": ""}

    def _gen_table():
        c = client_factory()
        try:
            views = call_json_light(
                c,
                "",
                STEP7_TABLE_VIEW_PROMPT.format(report=report_text),
                model="deepseek-v4-flash",
                max_tokens=4096,
            )
            table_views = normalize_table_views(views)
            selected_view = choose_table_view(table_views)
            print(
                f"  表格视图: {selected_view['row_dimension']} × {selected_view['column_dimension']}",
                flush=True,
            )
            resp = c.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": STEP7_TABLE_PROMPT.format(
                    report=report_text,
                    table_view=json.dumps(selected_view, ensure_ascii=False, indent=2),
                )}],
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
                    question: str = "", print_lock: threading.Lock = None,
                    use_evidence_pack: bool = True,
                    ai_rerank_chunks: bool = False) -> list[dict]:
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

            import re as _re
            def _normalize(s: str) -> str:
                # 修复连字，转小写
                s = s.replace('ﬁ', 'fi').replace('ﬂ', 'fl').replace('ﬀ', 'ff').replace('ﬃ', 'ffi').replace('ﬄ', 'ffl').lower()
                # 移除所有非字母数字字符（包括标点、换行连字符、智能引号、空格等）
                # \W+ 会把所有标点和空格全部替换掉
                return _re.sub(r'\W+', '', s)

            norm_text = _normalize(full_text)
            norm_q60 = _normalize(quote[:60])
            norm_q120 = _normalize(quote[:120])
            norm_qfull = _normalize(quote)

            if norm_q60 and norm_q60 in norm_text:
                continue
            if norm_q120 and norm_q120 in norm_text:
                continue
            if norm_qfull and norm_qfull in norm_text:
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
                                       force_refresh=True,
                                       use_evidence_pack=use_evidence_pack,
                                       ai_rerank_chunks=ai_rerank_chunks)
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


def _coerce_json_list(result) -> list:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def verify_claim_map(report_text: str, client, model: str) -> str:
    """Ver B0: extract a generic claim map, then check cross-claim logic."""
    print(f"\n── Ver B0: Claim-map 逻辑检查 ──", flush=True)

    try:
        extracted = call_json(
            client,
            "",
            CLAIM_MAP_EXTRACT_PROMPT.format(report=report_text[:30000]),
            model,
            65536,
        )
    except Exception as e:
        print(f"  ⚠ claim-map 抽取失败，跳过: {e}", flush=True)
        return f"## Claim-map 逻辑检查\n\n- ⚠️ claim-map 抽取失败，已降级使用常规逻辑验证: {e}\n\n"

    claims = _coerce_json_list(extracted)
    if not claims:
        print(f"  ✅ 未抽取到可检查的核心论断", flush=True)
        return ""

    normalized_claims = []
    for i, claim in enumerate(claims):
        refs = claim.get("evidence_refs", [])
        if not isinstance(refs, list):
            refs = []
        clean_refs = []
        for ref in refs:
            try:
                clean_refs.append(int(ref))
            except (TypeError, ValueError):
                continue
        normalized_claims.append({
            "index": i,
            "claim": str(claim.get("claim", ""))[:1000],
            "scope": str(claim.get("scope", ""))[:500],
            "evidence_refs": clean_refs,
            "certainty": str(claim.get("certainty", "unclear")),
            "location": str(claim.get("location", ""))[:300],
        })

    try:
        checked = call_json(
            client,
            "",
            CLAIM_MAP_CHECK_PROMPT.format(
                claim_map=json.dumps(normalized_claims, ensure_ascii=False, indent=2)
            ),
            model,
            65536,
        )
    except Exception as e:
        print(f"  ⚠ claim-map 检查失败，跳过: {e}", flush=True)
        return f"## Claim-map 逻辑检查\n\n- ⚠️ claim-map 检查失败，已降级使用常规逻辑验证: {e}\n\n"

    issues = _coerce_json_list(checked)
    if not issues:
        print(f"  ✅ claim-map 无问题 ({len(normalized_claims)} 条论断)", flush=True)
        return ""

    md = "## Claim-map 逻辑检查\n\n"
    for iss in issues:
        icon = "❌" if iss.get("severity") == "error" else "⚠️"
        rel = iss.get("relationship", "issue")
        claim_ids = iss.get("claim_indices", [])
        md += (
            f"- {icon} **{rel}** claims={claim_ids} "
            f"{str(iss.get('location', ''))[:40]}... → {iss.get('issue', '')}\n"
        )
    md += "\n"
    print(f"  {len(issues)} 处 claim-map 问题", flush=True)
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
                     all_results: list[dict], model: str, pass_num: int = 1, total_passes: int = 1) -> str:
    """Step 6: 根据验证反馈修正报告。"""
    print(f"\n── Step 6: 修正报告 (第 {pass_num}/{total_passes} 轮) ──", flush=True)

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
    parser.add_argument("collection", nargs="+", help="Zotero 论文集路径（如 '主题 > 子集'，可多个）")
    parser.add_argument("--question", "-q", required=True, help="研究问题")
    parser.add_argument("--output", "-o", default="synthesize_output", help="输出目录")
    parser.add_argument("--model", "-m", default="deepseek-v4-pro", help="模型名")
    parser.add_argument("--workers", "-w", type=int, default=5, help="并发数")
    parser.add_argument("--cache-dir", help="文本缓存目录（默认 output/cache）")
    parser.add_argument("--max-papers", type=int, default=0, help="最大处理论文数（0=无限制）")
    parser.add_argument("--skip-step1", action="store_true", help="跳过Step1，从已有 findings 继续")
    parser.add_argument("--skip-verify", action="store_true", help="跳过所有验证")
    parser.add_argument("--full-prefix", action="store_true",
                        help="Step1 使用旧模式：发送 PDF 文本前 80000 字符，而不是 EvidencePack")
    parser.add_argument("--ai-rerank-chunks", action="store_true",
                        help="Step1 可选：用 AI 对 EvidencePack 候选文本块重排（默认关闭，--full-prefix 时无效）")
    parser.add_argument("--max-fix-passes", type=int, default=2, help="修正报告的最大轮数（默认2）")
    parser.add_argument("--stop-after", choices=STOP_AFTER_CHOICES,
                        help="调试模式：在指定步骤完成后停止（step1/ver1/step2/step3/step4）")
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
            all_results = load_cached_findings_for_papers(
                papers,
                args.question,
                args.model,
                findings_dir,
                use_evidence_pack=not args.full_prefix,
                ai_rerank_chunks=args.ai_rerank_chunks and not args.full_prefix,
            )
        except RuntimeError as e:
            sys.exit(str(e))
        relevant_loaded = sum(1 for r in all_results if r.get("relevant"))
        print(f"   加载 {len(all_results)} 篇 findings，其中 {relevant_loaded} 篇相关", flush=True)
    else:
        findings_dir.mkdir(parents=True, exist_ok=True)
        all_results = step1_extract_all(client_factory, papers, args.question,
                                        args.model, cache_dir, args.workers, findings_dir,
                                        use_evidence_pack=not args.full_prefix,
                                        ai_rerank_chunks=args.ai_rerank_chunks and not args.full_prefix)

    relevant_papers = [r for r in all_results if r["relevant"]]
    if not relevant_papers:
        print("论文集无相关论文。退出。", flush=True)
        return
    if should_stop_after("step1", args.stop_after):
        print_stop_after("step1", output_dir)
        return

    # ── Ver 1: 验证发现 ──
    if not args.skip_verify:
        # Ver 1 本地字符串校验；失败时用主模型强制重提取。
        all_results = verify_findings(all_results, papers, client_factory,
                                       args.model,
                                       cache_dir, findings_dir,
                                       args.question,
                                       use_evidence_pack=not args.full_prefix,
                                       ai_rerank_chunks=args.ai_rerank_chunks and not args.full_prefix)
                                       
    # ── 导出 EvidencePack 覆盖率报告 ──
    coverage_report = []
    for r in all_results:
        if r.get("relevant") and "evidence_pack" in r:
            cov = r["evidence_pack"]
            coverage_report.append({
                "file": r["file"],
                "ref_num": r.get("ref_num"),
                "total_chars": cov.get("total_chars"),
                "pack_chars": cov.get("pack_chars"),
                "coverage_ratio": cov.get("coverage_ratio"),
                "chunks_used": len(cov.get("selected_chunks", [])),
                "ai_reranked": cov.get("ai_reranked", False)
            })
    if coverage_report:
        cov_path = output_dir / "evidence_coverage.json"
        cov_path.write_text(json.dumps(coverage_report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  📄 EvidencePack 覆盖率报告已保存: {cov_path}", flush=True)

    if should_stop_after("ver1", args.stop_after):
        print_stop_after("ver1", output_dir)
        return

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
    if should_stop_after("step2", args.stop_after):
        print_stop_after("step2", output_dir)
        return

    # ── Step 3 ──
    sections, paper_refs = step3_match_and_write(client_factory, outline, all_results, args.question, args.model, args.workers)
    sections_debug_path = output_dir / "sections.json"
    sections_debug_path.write_text(json.dumps({
        "sections": sections,
        "paper_refs": paper_refs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📄 分节草稿已保存: {sections_debug_path}", flush=True)

    if not sections:
        print("撰写失败，无内容产出。退出。", flush=True)
        return
    if should_stop_after("step3", args.stop_after):
        print_stop_after("step3", output_dir)
        return

    # ── Step 4 ──
    report = step4_integrate(client, outline, sections, args.question, paper_refs, args.model)

    report_path = output_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"  📄 报告已保存: {report_path}", flush=True)
    if should_stop_after("step4", args.stop_after):
        print_stop_after("step4", output_dir)
        return

    # ── Ver A/B: 引用+逻辑验证 ──
    has_issues = False
    verification_report = ""
    if not args.skip_verify:
        vp = verify_references_programmatic(report, paper_refs)
        va = verify_citations(report, all_results, client, args.model)
        vcm = verify_claim_map(report, client, args.model)
        vb = verify_logic(report, client, args.model)
        if vp or va or vcm or vb:
            has_issues = True
            verification_report = "\n\n---\n\n# 验证报告\n\n" + vp + va + vcm + vb
            verify_path = output_dir / "verification.md"
            verify_path.write_text(verification_report, encoding="utf-8")
            print(f"  📄 验证报告已保存: {verify_path}", flush=True)
        else:
            print(f"  ✅ 验证通过，无问题\n", flush=True)

    # ── Step 6: 修正报告（如验证发现问题）──
    if has_issues and args.max_fix_passes > 0:
        print(flush=True)
        for fix_pass in range(1, args.max_fix_passes + 1):
            report = step6_fix_report(client, report, verification_report, all_results, args.model, pass_num=fix_pass, total_passes=args.max_fix_passes)
            report = _clean_refs(report, paper_refs)
            report_path = output_dir / "report.md"
            report_path.write_text(report, encoding="utf-8")
            print(f"  📄 修正后报告已保存: {report_path}", flush=True)
            if not args.skip_verify:
                print(f"\n── Ver A/B: 修正后二次验证 (第 {fix_pass}/{args.max_fix_passes} 轮) ──", flush=True)
                vp2 = verify_references_programmatic(report, paper_refs)
                va2 = verify_citations(report, all_results, client, args.model)
                vcm2 = verify_claim_map(report, client, args.model)
                vb2 = verify_logic(report, client, args.model)
                if vp2 or va2 or vcm2 or vb2:
                    verification_report = f"\n\n---\n\n# 修正后二次验证报告 (第 {fix_pass} 轮)\n\n" + vp2 + va2 + vcm2 + vb2
                    second_path = output_dir / "verification_after_fix.md"
                    second_path.write_text(verification_report, encoding="utf-8")
                    print(f"  📄 修正后二次验证报告已保存: {second_path}", flush=True)
                    if fix_pass == args.max_fix_passes:
                        print(f"  ⚠ 经过 {args.max_fix_passes} 轮修正，仍有遗留问题，已追加到报告末尾。", flush=True)
                        report += "\n\n## 遗留问题与局限性\n\n"
                        report += "以下为自动验证步骤中发现且未能自动修复的逻辑或引用问题，供读者参考：\n\n"
                        report += (vp2 + va2 + vcm2 + vb2)
                        report_path.write_text(report, encoding="utf-8")
                else:
                    print(f"  ✅ 修正后二次验证通过，无问题\n", flush=True)
                    second_path = output_dir / "verification_after_fix.md"
                    if second_path.exists():
                        second_path.unlink()
                    break

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
