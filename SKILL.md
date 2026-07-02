---
name: review-assistant
description: 综述助手。用途：①Zotero 数据库查询与统计；②从 Zotero 论文集批量拆解 PDF 论文为结构化字段；③将综述段落分解为独立主张并逐一在原文中验证支持度；④给定研究问题探索论文集，生成报告、文章、表格和示意图；⑤自然语言检索 Semantic Scholar/PubMed 文献并导入 Zotero。触发场景："综述"、"拆解论文"、"验证这段文字"、"核对文献"、"研究一下"、"探索一下"、"帮我找文献"、"搜索XX文献"。
---

# Review Assistant Skill

This skill wraps the CLI tools in `scripts/`. Run commands from the skill root unless the package has been installed with `python -m pip install -e .`.

## Tool Map

| User intent | Preferred command |
|---|---|
| 查看 Zotero 里有哪些集合、每个集合多少 PDF | `python scripts/zotero_read.py --list` |
| 查看某个集合的文献和 PDF 覆盖 | `python scripts/zotero_read.py "集合 > 子集"` |
| 批量拆解论文为结构化笔记 | `python scripts/paper_breakdown.py -z "集合 > 子集" -o ./output -w 5` |
| 验证一段综述是否有文献支撑 | `python scripts/claim_verify.py "集合 > 子集" -p "段落文本" -o report.json` |
| 从论文集探索并生成综述 | `python scripts/explore_synthesize.py "集合 > 子集" -q "研究问题" -o ./synthesize_output` |
| 搜索新文献并导入 Zotero | `python scripts/auto_lit.py "english keywords" --web-import -c "集合 > 子集" -t tag` |

Console aliases after editable install:

```text
review-assistant-read
review-assistant-breakdown
review-assistant-verify
review-assistant-synthesize
review-assistant-autolit
```

## Before Running

Check only what is needed for the requested workflow:

- Python must be 3.10+.
- LLM workflows need `DEEPSEEK_API_KEY` or another OpenAI-compatible API key.
- Literature search needs `SS_API_KEY` for Semantic Scholar, or `PUBMED_API_KEY` / `NCBI_API_KEY` for PubMed.
- Zotero Web import needs `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_TYPE`, and `ZOTERO_LIBRARY_ID`.
- If Zotero data lives outside the default path, pass `--zotero-dir` or set `ZOTERO_DIR`.

Do not write directly to Zotero's local SQLite database. Read local Zotero data through `zotero_reader.py`; write new Zotero items through Web API import or RIS fallback.

## Workflow Details

### A. Inspect Zotero

Use this first when collection names or PDF coverage are unclear.

```bash
python scripts/zotero_read.py --list
python scripts/zotero_read.py "Collection > Subcollection"
python scripts/zotero_read.py --pdf-only "Collection > Subcollection"
```

For custom statistics beyond the CLI output, use `ZoteroReader` from `scripts/zotero_reader.py` rather than hand-parsing SQLite paths.

### B. Break Down Papers

```bash
python scripts/paper_breakdown.py \
  -z "Collection > Subcollection" \
  -o ./paper_breakdown_output \
  -w 5
```

Folder input is also supported:

```bash
python scripts/paper_breakdown.py -i /path/to/pdfs -o ./paper_breakdown_output
```

Output: one JSON note per paper plus `_summary.csv`.

### C. Verify Claims

```bash
python scripts/claim_verify.py \
  "Collection > Subcollection" \
  -f paragraph.md \
  -o claim_report.json
```

The verifier decomposes the paragraph into independent claims, matches each claim to relevant papers, checks source evidence, and labels support strength.

### D. Explore And Synthesize

```bash
python scripts/explore_synthesize.py \
  "Collection > Subcollection" \
  -q "Research question" \
  -o ./synthesize_output
```

Useful options:

| Option | Purpose |
|---|---|
| `--vector-search` | Use semantic vector search for chunk selection |
| `--skip-step1` | Continue from existing `findings/` cache |
| `--skip-verify` | Skip citation and logic verification |
| `--max-papers N` | Limit number of PDFs processed |
| `--step7-model MODEL` | Override model for table/Mermaid generation |

Typical output files:

```text
report.md
article.md
table.md
diagram.md
verification.md
verification_after_fix.md
outline.json
sections.json
findings/
cache/
evidence_coverage.json
```

### E. Search And Import Literature

Use short English keyword queries.

```bash
python scripts/auto_lit.py \
  "hybrid search reranking retrieval augmented generation" \
  --web-import \
  -c "Information Retrieval > RAG" \
  -t rag-review \
  -n 10
```

If Web API import is not configured, generate RIS instead:

```bash
python scripts/auto_lit.py "hybrid search reranking RAG" -o rag_candidates.ris
```

Screening can be customized with a JSON rules file:

```bash
python scripts/auto_lit.py \
  "topic keywords" \
  --screen \
  --screen-rules ./rules.json \
  -c "Target Collection" \
  -t topic-tag
```

## Portability Rules

Keep these constraints when extending the code:

1. Do not hard-code topic-specific screening rules in Python. Put configurable screening logic in data structures or JSON/YAML files.
2. Use `Path` and the existing Zotero path-resolution helpers for local file paths. Respect `ZOTERO_LINKED_BASE_DIR` and `ZOTERO_LINKED_PREFIX_MAP`.
3. Keep provider-specific LLM parameters behind `llm_client.py`; unsupported parameters should be stripped/retried there, not scattered through workflow scripts.
4. Avoid GUI side effects by default. Actions such as opening Zotero import dialogs should stay behind explicit flags.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `No module named ...` | Dependencies missing | `python -m pip install -r requirements.txt` |
| No PDFs found | Wrong collection path or missing attachments | Run `python scripts/zotero_read.py --list` |
| Scanned PDF extracts little text | No readable text layer | OCR or unlock the PDF first |
| API returns 429 | Rate limit | Lower `-w`, shorten search query, or wait |
| Zotero Web import fails with 401/403 | Bad key or missing write permission | Check `ZOTERO_API_KEY` and library settings |

## Docs

- [README.md](./README.md): user-facing overview
- [GUIDE.zh-CN.md](./GUIDE.zh-CN.md): Chinese walkthrough
- [CHANGELOG.md](./CHANGELOG.md): release notes
- [docs/README.md](./docs/README.md): documentation index and archive notes
