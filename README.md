# Review Assistant

> 中文用户看这里 → [GUIDE.zh-CN.md](./GUIDE.zh-CN.md)（人话版流程说明）

## About

Review Assistant is a Codex/OpenCode skill and local web app for literature-review workflows built around Zotero collections, local PDFs, and LLM-assisted synthesis.

It helps you move from a folder of papers to structured notes, claim verification, review reports, narrative articles, summary tables, and follow-up literature searches. The tool is designed for research workflows where source grounding matters: it reads local PDFs, extracts evidence with citations, verifies quotes against source text, and flags logical or citation issues before producing final outputs. Most workflows can be run from the CLI or from the React/FastAPI desktop web interface.

## What It Does

Review Assistant supports both the existing exploratory narrative workflow and a protocol-driven, auditable Review workflow. See [Protocol-driven Review mode](./docs/review-mode.md) for configuration, CLI commands, offline tutorial, artifacts, and migration guidance.

- **能力A · 文献拆解**: Decompose PDFs into structured paper notes.
- **能力B · 主张验证**: Verify claims in a paragraph against papers in a Zotero collection.
- **能力C · 数据库查询**: Browse Zotero collections and check local PDF coverage.
- **能力D · 探索总结**: Explore a research question across a paper set and synthesize a report.
- **能力E · 文献检索入库**: Search Semantic Scholar for literature and import through Zotero Web API or RIS fallback.
- **Web UI**: Browse Zotero, run verification/breakdown/synthesis tasks, and manage settings from a local desktop-oriented UI.

## Core Workflows

### 1. Inspect Zotero（能力C）

List all collections and PDF coverage:

```bash
python -m review_assistant.zotero_read --list
```

Inspect one collection:

```bash
python -m review_assistant.zotero_read "Collection > Subcollection"
python -m review_assistant.zotero_read --pdf-only "Collection > Subcollection"
```

### 2. Break Down Papers（能力A）

Create structured JSON notes and a CSV summary from PDFs in a Zotero collection:

```bash
python -m review_assistant.paper_breakdown -z "Collection > Subcollection" -o ./output -w 5
```

Or process a folder of PDFs directly:

```bash
python -m review_assistant.paper_breakdown -i /path/to/pdfs -o ./output
```

**Parameters:**

| Parameter | Description | Default |
|---|---|---|
| `-z` / `--zotero-collection` | Zotero collection path | — |
| `-i` / `--input-dir` | PDF folder path | — |
| `-o` / `--output` | Output directory | — |
| `-w` / `--workers` | Concurrent workers | `3` |
| `-m` / `--model` | LLM model | `deepseek-v4-flash` |
| `--list-collections` | List available collections and PDF coverage | — |

Already-processed papers are automatically skipped; safe to re-run. Output: `*.json` per paper + `_summary.csv`.

### 3. Verify Claims（能力B）

Check whether a paragraph's claims are supported by papers in a Zotero collection:

```bash
python -m review_assistant.claim_verify "Collection > Subcollection" -p "Your review paragraph..." -o report.json
```

Or from a file:

```bash
python -m review_assistant.claim_verify "Collection > Subcollection" -f paragraph.md -o report.json
```

**Parameters:**

| Parameter | Description | Default |
|---|---|---|
| `collection` | Zotero collection path (required) | — |
| `-p` / `--paragraph` | Paragraph text to verify | — |
| `-f` / `--file` | Read paragraph from file | — |
| `-m` / `--model` | LLM model | `deepseek-v4-flash` |
| `--top` | Max papers per claim | `3` |
| `-o` / `--output` | JSON report output path | terminal only |

The report decomposes the paragraph into independent claims, matches each claim to relevant papers, and labels support strength: fully supported / partially supported / weakly supported / not supported / contradictory.

### 4. Explore And Synthesize（能力D）

Run the full review pipeline — extract findings, build outline, write report, verify, generate article + table + diagram:

```bash
python -m review_assistant.explore_synthesize "Collection > Subcollection" -q "Your research question" -o ./output
```

With vector-search hybrid RAG:

```bash
python -m review_assistant.explore_synthesize "Collection > Subcollection" -q "Your research question" --vector-search -o ./output
```

**Parameters:**

| Parameter | Description | Default |
|---|---|---|
| `collection` | Zotero collection path (required, can be multiple) | — |
| `-q` / `--question` | Research question (required) | — |
| `-o` / `--output` | Output directory | `synthesize_output` |
| `-m` / `--model` | LLM model | `deepseek-v4-pro` |
| `-w` / `--workers` | Concurrent workers | `5` |
| `--skip-step1` | Skip extraction, reuse cached findings | — |
| `--skip-verify` | Skip all verification steps | — |
| `--full-prefix` | Use the legacy first-80000-characters input mode instead of EvidencePack | off |
| `--ai-rerank-chunks` | Let an LLM rerank EvidencePack candidate chunks when recall confidence is low | off |
| `--vector-search` | Enable hybrid vector + keyword chunk retrieval | off |
| `--max-papers` | Max papers to process (0 = unlimited) | `0` |
| `--stop-after` | Stop after `step1`, `ver1`, `step2`, `step3`, or `step4` for debugging | — |
| `--step7-model` | Model for summary table and diagram generation | `REVIEW_ASSISTANT_STEP7_MODEL` |
| `--input` / `-i` | Process PDFs from a local folder instead of Zotero | — |

See [Full Synthesis Pipeline](#full-synthesis-pipeline) below for the detailed step-by-step flow.

### 5. Search And Import Literature（能力E）

Search Semantic Scholar and import results into Zotero:

```bash
# Basic search with default screening:
python -m review_assistant.auto_lit "short English query" -c "Target Collection" -t "topic-tag" -n 10

# With custom screening rules:
python -m review_assistant.auto_lit "short English query" --screen --screen-rules ./rules.json -c "Target" -t "tag"

# Web API direct import:
python -m review_assistant.auto_lit "short English query" --web-import -c "Parent > Child" -t "tag"
```

**Parameters:**

| Parameter | Description | Default |
|---|---|---|
| `keywords` | Semantic Scholar search keywords (English) | — |
| `-c` / `--collection` | Target Zotero collection path | — |
| `-t` / `--tag` | Zotero tag | — |
| `-m` / `--min-citations` | Minimum citation count filter | `0` |
| `-n` / `--limit` | Max results | `20` |
| `--screen` | Enable title/abstract year-aware relevance screening | off |
| `--screen-rules` | Custom screening rules JSON file | built-in defaults |
| `--min-relevance` | Minimum relevance score in screen mode | `4` |
| `--import-zotero` | Auto-open Zotero for RIS import (macOS only) | off |
| `--web-import` | Import via Zotero Web API | off |
| `--zotero-library-type` | Web API library type: `user` or `group` | `ZOTERO_LIBRARY_TYPE` env |
| `--zotero-library-id` | Zotero user ID or group ID | `ZOTERO_LIBRARY_ID` env |
| `--collection-key` | Direct Zotero collection key (skip path resolution) | — |
| `--no-create-collection` | Fail if collection missing in web mode | auto-create |
| `--no-wait-local-sync` | Skip waiting for local Zotero sync after web import | wait |

**Year-aware screening:** Use `--screen` to score candidates by title, abstract, and journal — useful for cross-disciplinary or recent papers where citation thresholds are unreliable. RIS output includes `screen:A/B` and `score:N` keywords for Zotero review. Without `--screen`, `-m` behaves as a hard citation-count filter.

**Integration with synthesize:** After running `explore_synthesize.py`, review the summary table for `~无数据` cells. These represent gaps you can fill by searching with `auto_lit.py` and re-running the pipeline.

## Full Synthesis Pipeline

```text
Step1 -> Ver1 -> Step2 -> Step3 -> Step4 -> Ver A/B -> Step6 -> Step5 -> Step7
Extract  Quote   Outline  Match+Write  Integrate  Verify     Fix     Article  Table/Diagram
```

| Step | Description | Design Notes |
|---|---|---|
| **Step1** | Extract findings from each PDF: claim_cn, quote, cite_key, relation/context/variables, and dynamic tags. Irrelevant papers are skipped. | Uses EvidencePack by default: section-aware chunks, keyword windows, front/tail context, optional vector search and optional AI reranking; PDF text + API results are double-cached |
| **Ver1** | Verify each quote against source text (exact/fuzzy match). Retry extraction up to 2 rounds on failure. | Local matching, no API cost |
| **Step2** | LLM reviews all findings and generates a report outline with search_tags on leaf nodes. | Groups by population dimensions specified in the research question |
| **Step3** | Phase A: match up to 8 findings per section via relation/context/variables/tag semantics. Phase B: write each section in parallel. | Prefers direct findings; falls back to indirect/background only when needed; citations are locally sanitized against matched refs |
| **Step4** | Merge sections, polish style, generate references, flag cross-section contradictions. | Orphan references are auto-removed based on body citations; grouped citations like `[1, 2]` and ranges like `[1-3]` are supported |
| **Ver A** | Verify citation metadata: year, author names, core concept entities against findings index. | Uses reasoning model with 65536 tokens |
| **Ver B** | Check cross-section contradictions, conclusion leaps, and unsupported assertions. | Same reasoning model |
| **Step6** | Apply factual and logical fixes based on verification feedback. Remove irrelevant sections. | temperature=0, no meta-commentary |
| **Step5** | Convert structured report into a narrative review article. References preserved verbatim. | temperature=0.3 (only exception, for natural prose) |
| **Step7** | Generate summary table (row × column dimensions) and Mermaid diagram with color-coded relationships. | Table takes priority; auto-colored |

## Output Layout

Typical `explore_synthesize.py` output:

```text
synthesize_output/
├── cache/                  # PDF text cache (SHA256-named)
├── findings/               # Per-paper extracted findings
├── outline.json            # Generated outline
├── outline.meta.json       # Outline caching metadata
├── sections.json           # Cached section drafts
├── sections.meta.json      # Section drafts caching metadata
├── report.md               # Structured review report
├── report.meta.json        # Report caching metadata
├── article.md              # Narrative article
├── table.md                # Summary table
├── diagram.md              # Mermaid diagram
├── verification.md         # Citation and logic verification
├── verification_after_fix.md # Revised report verification
└── evidence_coverage.json  # EvidencePack coverage report
```

Mermaid diagrams can be rendered locally: `mmdc --input diagram.md --output diagram.svg --backgroundColor white`

Typical `paper_breakdown.py` output:

```text
paper_breakdown_output/
├── *.json             # One structured note per paper
└── _summary.csv       # Combined paper summary table
```

## Scripts

| Module Entry Point | Purpose |
|---|---|
| `python -m review_assistant.zotero_read` | Browse Zotero collections, item metadata, and PDF coverage. |
| `review_assistant.zotero_reader.ZoteroReader` | Read-only Zotero SQLite helper used by other modules. |
| `python -m review_assistant.paper_breakdown` | Batch PDF-to-structured-note extraction. |
| `python -m review_assistant.claim_verify` | Claim decomposition and source verification against a paper set. |
| `python -m review_assistant.explore_synthesize` | End-to-end research-question synthesis pipeline. |
| `python -m review_assistant.auto_lit` | Semantic Scholar/PubMed search to Zotero Web API import or RIS fallback. |

## Environment Variables

### Required

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API key for LLM workflows |
| `SS_API_KEY` | Semantic Scholar API key for literature search |
| `PUBMED_API_KEY` | PubMed API key (optional, raises rate limit) |

Additional DeepSeek keys (`DEEPSEEK_API_KEY_2`, `_3`, `_4`...) are auto-detected by the synthesis pipeline for key rotation.

### Optional

| Variable | Purpose | Default |
|---|---|---|
| `REVIEW_ASSISTANT_MODEL` | Default LLM model | `deepseek-v4-pro` |
| `REVIEW_ASSISTANT_STEP7_MODEL` | Model for Step7 table/diagram generation | same as main |
| `REVIEW_ASSISTANT_BASE_URL` / `DEEPSEEK_BASE_URL` | OpenAI-compatible API base URL | DeepSeek default |
| `REVIEW_ASSISTANT_EMBEDDING_API_KEY` | API key for text embeddings | `OPENAI_API_KEY` |
| `REVIEW_ASSISTANT_EMBEDDING_BASE_URL` | Base URL for text embeddings | OpenAI default |
| `REVIEW_ASSISTANT_WORKERS` | Default worker count | `5` |
| `REVIEW_ASSISTANT_USE_PROXY` | Set to `true` to preserve system proxy variables | stripped |
| `ZOTERO_DIR` | Custom Zotero data directory | `~/Zotero` |
| `ZOTERO_LINKED_BASE_DIR` | Base dir for Zotero linked-file relative paths | — |
| `ZOTERO_LINKED_PREFIX_MAP` | Cross-system drive-letter mapping (e.g. `C:\...\=>/Users/.../\|D:\...\=>/mnt/.../`) | — |
| `AUTO_LIT_LOCK_DIR` | Custom dir for Semantic Scholar cross-process lock file | — |
| `ZOTERO_API_KEY` | Zotero Web API key (write permission required) | — |
| `ZOTERO_LIBRARY_TYPE` | Web API library type: `user` or `group` | — |
| `ZOTERO_LIBRARY_ID` | Zotero user ID or group ID | — |
| `ZOTERO_WEB_IMPORT` | Set to `true` to default to Web API import | `false` |
| `ZOTERO_SYNC_TIMEOUT` | Seconds to wait for local Zotero sync after web import | — |

## Requirements

- Python 3.10+
- Zotero with papers organized in collections
- Local PDF attachments for full-text workflows
- DeepSeek or OpenAI-compatible API key
- Semantic Scholar API key for search/import workflows
- PubMed API key / NCBI API key (optional, for higher rate limits)

## Installation & Setup

1. **Install Python dependencies:**

```bash
python -m pip install -r requirements.txt
```

Or install locally in editable mode:

```bash
python -m pip install -e .
```

2. **Configure API keys:**

```bash
export DEEPSEEK_API_KEY="your-key"
export SS_API_KEY="your-key"
export PUBMED_API_KEY="your-key"  # Optional
```

Or source an env file: `source ~/Documents/api.env`

See [Environment Variables](#environment-variables) for the full list.

## Web UI

The web interface is intended for desktop use. It consists of a FastAPI backend and a Vite/React frontend.

Start the API server:

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```bash
cd web
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. In development, Vite proxies `/api/*` requests to `http://localhost:8000` by default. Override this with `VITE_API_URL` if needed.

Build the production frontend:

```bash
cd web
npm run build
```

The FastAPI app serves `web/dist` when the frontend has been built.

## Testing

```bash
pytest -q
python -m compileall -q review_assistant tests
cd web && npm run build
cd web && npm run lint
```

For browser-level desktop smoke testing, start both servers and verify the main routes (`/`, `/verify`, `/breakdown`, `/synthesize`, `/settings`) with Playwright or another browser automation tool. The expected desktop checks are: route title matches, active navigation item matches, no horizontal overflow, and no application console errors when the backend is running.

## Design Notes

- Zotero access is read-only: scripts copy the SQLite database without requiring Zotero to close.
- PDF coverage is checked against actual local files, not only Zotero attachment records.
- Semantic Scholar requests are rate-limited with a platform-adaptive cross-process lock (fcntl on Unix, msvcrt on Windows).
- PDF text and LLM findings are cached to make reruns cheaper and reproducible.
- Analytical extraction uses deterministic model settings (temperature=0) wherever practical.
- The synthesis pipeline favors grounded findings over broad, unsupported narrative generation.
- Multi-key rotation: synthesis auto-detects `DEEPSEEK_API_KEY_2`, `_3`, `_4`... to bypass single-key rate limits.
- API compatibility fallback: OpenAI-compatible providers sometimes disagree on `reasoning_effort`, `thinking`, `temperature`, `max_tokens`, and `max_completion_tokens`; the LLM wrapper retries once with compatible parameters when those errors occur.
- JSON parsing accepts both top-level objects and arrays, including fenced JSON blocks, so verifier prompts that return `[]` do not fail.
- Orphan references are auto-cleaned: only references cited in the body text via `[N]`, `[N, M]`, or `[N-M]` appear in the final bibliography.
- The React UI renders only the active route, avoiding hidden-page API calls and stale hidden state.

## Generality & Portability

- **Customizable Screening Rules**: Define keywords, weights (including negative values for exclusion), and categorization tiers in a JSON config file via `--screen-rules`. Falls back to built-in default rules when no file is specified.
- **Cross-Platform Zotero Paths**: Windows drive letters (`C:\...`) and linked-file attachments are resolved on macOS/Linux via `ZOTERO_LINKED_BASE_DIR` and `ZOTERO_LINKED_PREFIX_MAP`.
- **Multi-LLM Compatibility**: The backend detects model families and retries with provider-compatible chat parameters when OpenAI-compatible APIs reject reasoning, thinking, temperature, or token-limit fields.

## Limitations

- Scanned or encrypted PDFs may need OCR or unlocking first.
- RIS import is semi-automatic and may require confirming the Zotero import dialog; Web API import avoids this.
- The local Zotero API does not support write requests; silent local collection creation is not implemented.
- Generated reports should still be reviewed by a human, especially for high-stakes or publishable work.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No module named ...` / `No module named 'pymupdf'` | Missing Python dependencies | `pip install -r requirements.txt` |
| No PDFs found / `论文集没有可用的 PDF` | Collection empty or lacks PDF attachments | Run `python -m review_assistant.zotero_read --list` to check coverage |
| API key missing | Key not exported in environment | Confirm `DEEPSEEK_API_KEY`, `SS_API_KEY` are set |
| `database is locked` | Zotero is writing to SQLite | Wait for Zotero to finish, then retry (reads unaffected) |
| Cannot extract PDF text (0 chars) | Scanned or encrypted PDF | OCR the PDF first, or unlock it |
| API 429 Too Many Requests | Rate limit hit | Reduce `-w` concurrency; shorten SS query to ≤5 words |
| SS search 429 | Semantic Scholar rate limit | Shorten query to ≤5 words, wait 1-2 hours |
| `Insufficient Balance` | DeepSeek API balance exhausted | Top up, then re-run failed steps |
| JSON parse failure on breakdown | LLM did not output valid JSON | Re-run single paper or switch model; JSON objects and arrays are both supported, but malformed text still fails |
| `API returned empty response` | Reasoning tokens crowded out output | Fixed: verification uses 65536 tokens |
| All results `~无数据` | Papers not relevant to question | Use a more specific question, or search for relevant literature first |
| Same paper re-processing repeatedly | Cache version changed or PDF content changed | Normal behavior; let it complete and re-cache |
| API balance runs out mid-run | Pay-as-you-go billing | Top up, then resume from checkpoint with `--skip-step1` |
| Linked attachment not found | Path mismatch between systems | Set `ZOTERO_LINKED_BASE_DIR` or `ZOTERO_LINKED_PREFIX_MAP` |
| Same-named PDFs cause citation mix-up | Two papers both named `fulltext.pdf` | Fixed: now uses full path to disambiguate |
| Reference numbers don't match in report | Orphan references auto-cleaned in Step 4 | Only references actually cited in body appear in bibliography |
| Web UI shows API 502 in dev | Vite is running without the FastAPI backend | Start `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000` |
| Settings page says failed to load settings | Backend unavailable or settings endpoint failed | Check the API server logs and `/api/health` |

## Repository Structure

```text
.
├── SKILL.md                   # Codex/OpenCode skill instructions
├── README.md                  # User-facing project documentation
├── GUIDE.zh-CN.md             # 中文人话指南
├── CHANGELOG.md               # Release changelog
├── requirements.txt
├── pyproject.toml
├── api/                       # FastAPI backend
├── review_assistant/          # Python package and CLI modules
├── tests/                     # Python regression tests
├── web/                       # Vite/React frontend
├── docs/                      # Design notes and archived specs
└── evals/
```
