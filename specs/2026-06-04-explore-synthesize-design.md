# explore_synthesize.py Design

## Overview

新增探索总结工作流脚本 `explore_synthesize.py`，与现有 `paper_breakdown.py`、`claim_verify.py` 并列。
给定**研究问题**和**Zotero论文集**，直接从论文全文提取答案，生成结构化报告和叙事文章。

## Relationship to Existing Workflows

| 工作流 | 脚本 | 做什么 | 局限 |
|--------|------|--------|------|
| 拆解 | `paper_breakdown.py` | PDF→12字段摘要 | 丢失原文细节 |
| 验证主张 | `claim_verify.py` | 验证给定段落是否被文献支持 | 需要外部提供主张 |
| **探索总结** | `explore_synthesize.py` | 研究问题→报告+文章 | 新增，互补 |

## Architecture

5-step Map-Reduce pipeline，防止上下文溢出导致模型降智：

```
研究问题 ──→ [Step1: 逐篇提取] ──→ [Step2: 生成大纲] ──→ [Step3: 分节合成] ──→ [Step4: 整合报告] ──→ [Step5: 叙事文章]
             30次并发            1次                    N次 (N=叶子节点)    1次                   1次
```

### 关键设计：上下文压缩

- Step1 将 30×50000≈150 万字符原始全文压缩为约 3 万字符的 finding 摘要
- Step2~5 全部在压缩空间内操作，每步输入精炼
- 分节合成时每节仅涉及 3~8 条 findings，几百字

## Step Details

### Step 1: 逐篇提取 (30 API calls, concurrent)

**输入:** 单篇 PDF 全文 (PyMuPDF 提取) + 研究问题
**输出:**

```json
{
  "relevant": true,
  "findings": [
    {
      "claim_cn": "中文摘要",
      "quote": "原文关键句",
      "tags": {"key1": "value1", ...}
    }
  ]
}
```

- `tags` 维度由 LLM 根据研究问题自行决定，不硬编码
- 缓存 PDF→text 提取结果到 `--cache-dir/<sha>.txt`，API 结果不缓存
- 不相关论文标记 `relevant: false`，终端显示统计

### Step 2: 生成大纲 (1 API call)

**输入:** 所有相关 findings 的 claim_cn + tags
**输出:** 大纲 JSON，每层节点带 `search_tags` 用于后续匹配：

```json
{
  "title": "报告标题",
  "sections": [
    {
      "heading": "一级议题",
      "subsections": [
        {
          "heading": "子议题",
          "search_tags": {"指标": "CMRglc", "人群": "正常成年人"}
        }
      ]
    }
  ]
}
```

- 大纲由 LLM 根据实际发现动态生成，不硬编码结构
- `search_tags` 格式灵活，与 Step1 的 tags 自由匹配

### Step 3: 分节合成 (N API calls)

每个叶子节点：
1. 用该节点的 `search_tags` + LLM 语义匹配筛选相关 findings（1次轻量API调用，输入为search_tags + 所有findings的claim_cn+tags列表，输出匹配的finding索引列表）
2. 匹配到的 findings 送 LLM 写该节正文（1次API调用）
3. 正文要求：报告体，附原文引用 (quote)

**输入量:** 每节 3-8 条 findings，几百字，不会降智。

### Step 4: 整合报告 (1 API call)

拼接各节 → LLM 润色统一风格 → 完整 Markdown 结构化报告。

### Step 5: 叙事文章 (1 API call)

结构化报告 → LLM 改写为流畅综述文章风格。

## CLI Interface

```
python explore_synthesize.py <Zotero选集路径> -q "研究问题" [options]

Options:
  -q, --question    研究问题（必填）
  -o, --output      输出目录（默认 ./synthesize_output）
  -m, --model       模型名（默认 deepseek-chat）
  -w, --workers     并发数（默认 5）
  --cache-dir       全文缓存目录（默认 ./synthesize_output/cache）
  --max-papers      最大处理论文数（默认0=无限制）
```

## Outputs

```
synthesize_output/
├── cache/                    # PDF 全文缓存
│   └── <sha256>.txt
├── findings/                 # Step1 单篇结果
│   └── <paper_title>.json
├── outline.json              # Step2 大纲
├── report.md                 # Step4 结构化报告
└── article.md                # Step5 叙事文章
```

## Terminal Output (Real-time)

```
📚 Zotero「电波 > alpha」: 30 篇 PDF

── Step 1: 逐篇提取 ──
  [1/30] Schreckenberger 2004 → ✓ 相关, 2条发现
  [2/30] Larson 1998 → ✓ 相关, 1条发现
  [3/30] Omata 2013 → ✗ 不相关
  ...
  结果: 24篇相关, 6篇不相关, 共87条发现

── Step 2: 生成大纲 ──
  📋 报告大纲:
    ├─ Alpha功率与葡萄糖代谢的关系
    │   ├─ 正常成年人
    │   └─ MCI/AD患者
    ├─ Alpha功率与BOLD/CBF的关系
    │   ├─ 正常成年人
    │   └─ MCI/AD患者
    └─ ...

── Step 3: 分节合成 ──
  [1/8] Alpha功率与葡萄糖代谢 > 正常成年人 → 完成 (3条发现)
  [2/8] Alpha功率与葡萄糖代谢 > MCI/AD → 完成 (5条发现)
  ...

── Step 4: 整合报告 ──
  ✅ 报告已保存: synthesize_output/report.md

── Step 5: 叙事文章 ──
  ✅ 文章已保存: synthesize_output/article.md
```

## Prompts

### Step1: 单篇提取

```
你是一位严谨的学术研究员。请阅读以下论文全文，判断是否包含与研究问题直接相关的发现。

研究问题：{question}

如果相关，提取每条独立发现，输出 JSON。每条发现的 tags 维度由你根据研究问题性质灵活决定，不要硬编码固定字段。

论文全文：
{full_text}

输出 JSON：
{
  "relevant": true/false,
  "findings": [
    {
      "claim_cn": "中文总结（1-2句）",
      "quote": "原文证据（原文语言）",
      "tags": {"维度1": "值", "维度2": "值", ...}
    }
  ]
}
只输出 JSON。
```

### Step2: 生成大纲

```
你是一位综述作者。以下是从多篇论文中提取的发现摘要。请据此生成一篇结构化报告的大纲。

研究问题：{question}

发现摘要：
(claim_cn列表，每条附tags)

输出 JSON 大纲，每层节点带 search_tags 用于筛选相关发现：
{
  "title": "报告标题",
  "sections": [
    {
      "heading": "...",
      "subsections": [
        {"heading": "...", "search_tags": {"键": "值"}}
      ]
    }
  ]
}
只输出 JSON。
```

### Step3: 分节合成

```
你是一位严谨的综述作者。请根据以下发现，撰写报告的一个章节。

章节主题：{section_heading}
研究问题：{question}

相关发现：
{matched_findings}

请用报告体撰写该节，每条结论附原文引用。语言：中文。
```

### Step4: 整合

```
合并以下报告各节为一份完整的结构化 Markdown 报告。统一格式风格，修正不一致的表述。不要删减内容。
{concatenated_sections}
```

### Step5: 叙事文章

```
将以下结构化报告改写为流畅的学术综述文章。保留所有核心结论和引用，语言连贯自然。中文。
{report}
```

## Dependencies

- `zotero_reader.py` (复用，不改)
- `pymupdf` (PDF 文本提取)
- `openai` (DeepSeek API)
- `hashlib` (全文缓存 key)

## Error Handling

| 场景 | 处理 |
|------|------|
| 论文集无相关论文 | 终端提示，退出 |
| 单篇 PDF 提取失败 | 跳过，记日志 |
| API 调用失败 | 重试 1 次，仍失败则跳过并警告 |
| 大纲无叶子节点 | 回退为简单结构：正常/MCI 两分组 |

## Self-Review Checklist

- [x] 标签维度由 LLM 动态决定，不硬编码
- [x] 每步输入量可控，不会超上下文
- [x] 与现有脚本接口一致 (-q, -o, -m, -w)
- [x] 缓存策略合理（全文缓存，API 结果不缓存）
- [x] 终端实时展示进度和关键信息
- [x] 输出结构化报告 + 叙事文章
