# Review Assistant

Review Assistant is a Codex/OpenCode skill and CLI toolkit for literature-review workflows around Zotero collections, local PDFs, and LLM-assisted synthesis.

It can inspect Zotero collections, extract structured notes from PDFs, verify review claims against source papers, synthesize reports from a research question, and search/import missing literature through Semantic Scholar, PubMed, Zotero Web API, or RIS fallback.

For a conversational Chinese walkthrough, see [GUIDE.zh-CN.md](./GUIDE.zh-CN.md). For archive material and completed design notes, see [docs/README.md](./docs/README.md).

## Capabilities

| Workflow | Command | Output |
|---|---|---|
| Inspect Zotero collections | `review-assistant-read` | Collection list, item metadata, PDF coverage |
| Break down papers | `review-assistant-breakdown` | Per-paper JSON notes and `_summary.csv` |
| Verify claims | `review-assistant-verify` | JSON/Markdown support report |
| Synthesize a review | `review-assistant-synthesize` | Report, article, table, Mermaid diagram, verification files |
| Search and import literature | `review-assistant-autolit` | Zotero Web API import or RIS file |

## Install

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
```

Editable install exposes these console commands:

```text
review-assistant-read
review-assistant-breakdown
review-assistant-verify
review-assistant-synthesize
review-assistant-autolit
```

You can also run scripts directly with `python scripts/<script>.py`.

## Configuration

At minimum, LLM-backed workflows need an OpenAI-compatible key:

```powershell
$env:DEEPSEEK_API_KEY="your-key"
```

Common environment variables:

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` | LLM API key |
| `REVIEW_ASSISTANT_BASE_URL` / `DEEPSEEK_BASE_URL` | OpenAI-compatible API base URL |
| `REVIEW_ASSISTANT_MODEL` | Default model for extraction, verification, and synthesis |
| `REVIEW_ASSISTANT_STEP7_MODEL` | Model for table and Mermaid generation |
| `REVIEW_ASSISTANT_WORKERS` | Default concurrency |
| `REVIEW_ASSISTANT_USE_PROXY=true` | Preserve system proxy variables |
| `ZOTERO_DIR` | Zotero data directory |
| `ZOTERO_LINKED_BASE_DIR` | Base directory for Zotero linked-file attachments |
| `ZOTERO_LINKED_PREFIX_MAP` | Cross-system path mapping, e.g. `C:\Users\me\=>/Users/me/` |
| `SS_API_KEY` | Semantic Scholar API key |
| `PUBMED_API_KEY` / `NCBI_API_KEY` | PubMed API key |
| `ZOTERO_API_KEY` | Zotero Web API key with write access |
| `ZOTERO_LIBRARY_TYPE` | `user` or `group` |
| `ZOTERO_LIBRARY_ID` | Zotero user ID or group ID |
| `ZOTERO_WEB_IMPORT=true` | Use Zotero Web API import by default |
| `ZOTERO_SYNC_TIMEOUT` | Seconds to wait for local Zotero sync |

## Quick Start

Inspect Zotero:

```bash
review-assistant-read --list
review-assistant-read "Collection > Subcollection"
review-assistant-read --pdf-only "Collection > Subcollection"
```

Break down papers:

```bash
review-assistant-breakdown \
  -z "Collection > Subcollection" \
  -o ./paper_breakdown_output \
  -w 5
```

Verify a paragraph:

```bash
review-assistant-verify \
  "Collection > Subcollection" \
  -p "Your review paragraph..." \
  -o claim_report.json
```

Run the synthesis pipeline:

```bash
review-assistant-synthesize \
  "Collection > Subcollection" \
  -q "What does this literature show about the research question?" \
  -o ./synthesize_output
```

Use semantic vector search for chunk selection:

```bash
review-assistant-synthesize \
  "Collection > Subcollection" \
  -q "What does this literature show about the research question?" \
  --vector-search \
  -o ./synthesize_output
```

Search and import literature:

```bash
review-assistant-autolit \
  "hybrid search reranking retrieval augmented generation" \
  --web-import \
  -c "Information Retrieval > RAG" \
  -t rag-review \
  -n 10
```

Without `--web-import`, the tool writes an RIS file that can be imported manually into Zotero.

## Synthesis Outputs

Typical `review-assistant-synthesize` output:

```text
synthesize_output/
|-- report.md
|-- article.md
|-- table.md
|-- diagram.md
|-- verification.md
|-- verification_after_fix.md
|-- outline.json
|-- sections.json
|-- findings/
|-- cache/
`-- evidence_coverage.json
```

## Testing

```bash
python -m unittest discover -s tests -v
```

## Repository Layout

```text
.
|-- SKILL.md              # Codex/OpenCode skill instructions
|-- README.md             # Project overview and CLI usage
|-- GUIDE.zh-CN.md        # Chinese human-friendly guide
|-- CHANGELOG.md
|-- docs/
|   `-- archive/          # Completed TODOs and historical design specs
|-- scripts/
|-- tests/
`-- evals/
```

## Limitations

- PDF extraction depends on readable text layers; scanned/encrypted PDFs may need OCR or unlocking first.
- Zotero Web API import creates metadata items, but PDF download remains a Zotero/Desktop/plugin/user step.
- Generated reviews should still be checked by a human before publication or high-stakes use.
