# Review Assistant

> 中文用户看这里 → [GUIDE.zh-CN.md](./GUIDE.zh-CN.md)（人话版流程说明）

## About

Review Assistant is a Codex/OpenCode skill for literature-review workflows built around Zotero collections, local PDFs, and LLM-assisted synthesis.

It helps you move from a folder of papers to structured notes, claim verification, review reports, narrative articles, summary tables, and follow-up literature searches. The tool is designed for research workflows where source grounding matters: it reads local PDFs, extracts evidence with citations, verifies quotes against source text, and flags logical or citation issues before producing final outputs.

## What It Does

- Browse Zotero collections and check local PDF coverage.
- Decompose PDFs into structured paper notes.
- Verify claims in a paragraph against papers in a Zotero collection.
- Explore a research question across a paper set and synthesize a report.
- Generate a narrative review article, summary table, and Mermaid diagram.
- Search Semantic Scholar for missing literature and import through Zotero Web API or RIS fallback.
- Cache PDF text and extraction results so repeated runs do not waste API calls.

## Core Workflows

### 1. Inspect Zotero

List all collections and PDF coverage:

```bash
# Using CLI script:
review-assistant-read --list

# Or using direct python execution:
python scripts/zotero_read.py --list
```

Inspect one collection:

```bash
review-assistant-read "Collection > Subcollection"
review-assistant-read --pdf-only "Collection > Subcollection"
```

### 2. Break Down Papers

Create structured JSON notes and a CSV summary from PDFs in a Zotero collection:

```bash
# Set environment variables, then run:
review-assistant-breakdown \
  -z "Collection > Subcollection" \
  -o ./paper_breakdown_output \
  -w 5
```

You can also process a folder of PDFs:

```bash
review-assistant-breakdown \
  -i /path/to/pdfs \
  -o ./paper_breakdown_output
```

### 3. Verify Claims

Check whether a paragraph is supported by papers in a Zotero collection:

```bash
review-assistant-verify \
  "Collection > Subcollection" \
  -p "Your review paragraph..." \
  -o claim_report.json
```

The report decomposes the paragraph into independent claims, matches each claim to relevant papers, and labels support strength.

### 4. Explore And Synthesize

Run the full review pipeline:

```bash
review-assistant-synthesize \
  "Collection > Subcollection" \
  -q "What does this literature show about the research question?" \
  -o ./synthesize_output
```

The pipeline extracts findings from every available PDF, builds an outline, writes a structured report, verifies citations and logic, applies fixes, then produces a narrative article plus table and diagram outputs.

### 5. Search And Import Literature

Search Semantic Scholar and import into Zotero. The default behavior generates an RIS file; `--web-import` writes directly through the Zotero Web API and can wait for Zotero Desktop to sync locally:

```bash
review-assistant-autolit \
  "short English search query" \
  -c "Target Zotero Collection" \
  -t "topic-tag" \
  -n 10
```

Direct Web API import:

```bash
review-assistant-autolit \
  "short English search query" \
  --web-import \
  --zotero-library-type user \
  --zotero-library-id "<your-user-id>" \
  -c "Parent > Child Collection" \
  -t "topic-tag"
```

`auto_lit.py` uses DOI-based de-duplication against the local Zotero database when possible.


## Scripts

| Script | Purpose |
|---|---|
| `scripts/zotero_read.py` | Browse Zotero collections, item metadata, and PDF coverage. |
| `scripts/zotero_reader.py` | Read-only Zotero SQLite helper used by other scripts. |
| `scripts/paper_breakdown.py` | Batch PDF-to-structured-note extraction. |
| `scripts/claim_verify.py` | Claim decomposition and source verification against a paper set. |
| `scripts/explore_synthesize.py` | End-to-end research-question synthesis pipeline. |
| `scripts/auto_lit.py` | Semantic Scholar/PubMed search to Zotero Web API import or RIS fallback. |

## Full Synthesis Pipeline

```text
Step1 -> Ver1 -> Step2 -> Step3 -> Step4 -> Ver A/B -> Step6 -> Step5 -> Step7
Extract  Quote   Outline  Match+Write  Integrate  Verify     Fix     Article  Table/Diagram
```

- **Step1:** Extract findings from each PDF as claims, quotes, citation keys, and dynamic tags.
- **Ver1:** Verify extracted quotes with local string or fuzzy matching.
- **Step2:** Generate a report outline from the extracted findings.
- **Step3:** Match findings to outline sections and write sections in parallel.
- **Step4:** Integrate sections into a structured report and remove orphan references.
- **Ver A/B:** Check citation metadata and logical consistency.
- **Step6:** Apply factual and logical fixes based on verification feedback.
- **Step5:** Convert the structured report into a narrative article.
- **Step7:** Generate a summary table and Mermaid diagram.

## Output Layout

Typical `explore_synthesize.py` output:

```text
synthesize_output/
├── report.md               # Structured review report
├── report.meta.json        # Report caching metadata
├── article.md              # Narrative article
├── table.md                # Summary table
├── diagram.md              # Mermaid diagram
├── verification.md         # Citation and logic verification
├── verification_after_fix.md # Revised report verification (if issues are fixed)
├── outline.json            # Generated outline
├── outline.meta.json       # Outline caching metadata
├── sections.json           # Cached section drafts
├── sections.meta.json      # Section drafts caching metadata
├── findings/               # Per-paper extracted findings
├── cache/                  # PDF text cache
└── evidence_coverage.json  # EvidencePack coverage report
```

Typical `paper_breakdown.py` output:

```text
paper_breakdown_output/
├── *.json             # One structured note per paper
└── _summary.csv       # Combined paper summary table
```

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

Or install the package locally in editable mode so you can run the `review-assistant-*` commands directly from anywhere:

```bash
python -m pip install -e .
```

2. **Load API keys / Environment variables:**

- **On Unix (macOS/Linux):**
  Create/load an environment file:
  ```bash
  export DEEPSEEK_API_KEY="your-key"
  export SS_API_KEY="your-key"
  export PUBMED_API_KEY="your-key"  # Optional: for PubMed search limit
  ```
  Or source a file: `source ~/Documents/api.env`

- **On Windows (Command Prompt):**
  ```cmd
  set DEEPSEEK_API_KEY="your-key"
  set SS_API_KEY="your-key"
  set PUBMED_API_KEY="your-key"
  ```

- **On Windows (PowerShell):**
  ```powershell
  $env:DEEPSEEK_API_KEY="your-key"
  $env:SS_API_KEY="your-key"
  $env:PUBMED_API_KEY="your-key"
  ```


3. **Optional configurations:**
- `ZOTERO_DIR`: Custom Zotero data directory (defaults to `~/Zotero`).
- `ZOTERO_LINKED_BASE_DIR`: Base directory to resolve Zotero linked file relative attachments (`attachments:relative/path.pdf`).
- `ZOTERO_LINKED_PREFIX_MAP`: Cross-system Windows drive-letter mapping, e.g. `C:\Users\...\= >/Users/.../|D:\Data\=>/mnt/data/`, used when reading a Zotero DB created on a different OS.
- `AUTO_LIT_LOCK_DIR`: Custom directory to place the Semantic Scholar cross-process lock file.
- `ZOTERO_API_KEY`: Zotero Web API key with write permission.
- `ZOTERO_LIBRARY_TYPE`: Zotero Web API library type, `user` or `group`.
- `ZOTERO_LIBRARY_ID`: Zotero user ID or group ID.
- `ZOTERO_WEB_IMPORT`: Set to `true` to use Web API import by default.
- `ZOTERO_SYNC_TIMEOUT`: Seconds to wait for local Zotero Desktop sync after Web API import.
- `REVIEW_ASSISTANT_BASE_URL` / `DEEPSEEK_BASE_URL`: Default OpenAI-compatible API base URL.
- `REVIEW_ASSISTANT_MODEL`: Default model for LLM-backed workflows.
- `REVIEW_ASSISTANT_STEP7_MODEL`: Default model for synthesis table and Mermaid generation.
- `REVIEW_ASSISTANT_WORKERS`: Default worker count for concurrent workflows.
- `REVIEW_ASSISTANT_USE_PROXY=true`: Preserve system proxy variables instead of stripping/bypassing them.

## Testing

To run the unit test suite:

```bash
python -m unittest discover -s tests -v
```


Optional:

```bash
DEEPSEEK_API_KEY_2=...
DEEPSEEK_API_KEY_3=...
```

Additional DeepSeek keys are auto-detected by the synthesis pipeline for key rotation.

## Design Notes

- Zotero access is read-only: the scripts copy or read the SQLite database without requiring Zotero to close.
- PDF coverage is checked against actual local files, not only Zotero attachment records.
- Semantic Scholar requests are rate-limited with a platform-adaptive cross-process lock (using fcntl on Unix and msvcrt on Windows).
- PDF text and LLM findings are cached to make reruns cheaper and reproducible.
- Analytical extraction uses deterministic model settings where practical.
- The synthesis pipeline favors grounded findings over broad, unsupported narrative generation.

## Limitations

- The tools depend on the quality of PDF text extraction. Scanned or encrypted PDFs may need OCR or unlocking first.
- RIS import into Zotero is semi-automatic and may require confirming the Zotero import dialog; Web API import avoids this when configured.
- The local Zotero API does not support write requests, so truly silent local collection creation is not implemented.
- Generated reports should still be reviewed by a human, especially for high-stakes or publishable work.

## Troubleshooting

- **`No module named ...`**: install dependencies with `python -m pip install -r requirements.txt`.
- **No PDFs found**: run `review-assistant-read --list` and confirm the collection path and PDF coverage.
- **API key missing**: confirm that the key is exported in your environment.

- **Semantic Scholar 429**: reduce search frequency or shorten the query.
- **Poor PDF extraction**: OCR the PDF and rerun the workflow.

## Repository Structure

```text
.
├── SKILL.md                   # Codex/OpenCode skill instructions
├── README.md                  # User-facing project documentation
├── GUIDE.zh-CN.md             # 中文人话指南（流程、能力、常见坑）
├── CHANGELOG.md               # Release changelog
├── TODO.md                    # Development roadmap
├── requirements.txt
├── pyproject.toml
├── scripts/
├── specs/
├── tests/
└── evals/
```
