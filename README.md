# Review Assistant

An [opencode](https://opencode.ai) skill for automated literature review — from searching to synthesis, all in one pipeline.

## What it does

Given a Zotero collection and a research question, it:

1. Extracts relevant findings from every PDF (38→42/87 papers typically)
2. Generates a structured outline based on discovered patterns
3. Writes a full review report with inline citations and contradiction analysis
4. Produces a narrative article, summary table, and Mermaid diagram
5. Verifies citation accuracy and logical consistency
6. Auto-fixes detected issues

When gaps are found (e.g., "no CBF data for healthy aging"), it searches Semantic Scholar, imports new papers into Zotero, and re-runs to fill them.

## Quick Start

```bash
# 1. Set up
source ~/Documents/api.env          # DeepSeek API key
export SS_API_KEY="your-ss-key"     # Semantic Scholar API key (free)

# 2. Explore a collection
cd ~/.agents/skills/review-assistant
python3 scripts/explore_synthesize.py "电波 > alpha" "电波 > alpha > 轻度认知障碍" \
  -q "How does alpha EEG power couple with brain metabolism across populations?" \
  -o ~/sciencing/synthesize_output
```

## Capabilities

| Script | Purpose |
|---|---|
| `explore_synthesize.py` | Full pipeline: PDF extraction → outline → report → article → table → diagram |
| `auto_lit.py` | Natural language → Semantic Scholar search → RIS import to Zotero |
| `paper_breakdown.py` | Batch decompose PDF papers into structured JSON fields |
| `claim_verify.py` | Verify claims in a paragraph against Zotero papers |
| `zotero_read.py` | Browse Zotero collections and statistics |
| `zotero_reader.py` | Programmatic Zotero SQLite queries |

## Pipeline (7 steps + 3 verifications)

```
Step1 ─→ Ver1 ─→ Step2 ─→ Step3 ─→ Step4 ─→ Ver A/B ─→ Step6 ─→ Step5 ─→ Step7
Extract   Verify   Outline  Match+Write  Integrate  Citation+Logic  Fix  Article  Table+Diagram
```

**Step1** — Extract findings per paper (claim + quote + cite_key + dynamic tags)\
**Ver1** — Verify quotes against source text (local string match)\
**Step2** — LLM generates outline from all findings\
**Step3** — Match findings to sections, write each section in parallel\
**Step4** — Integrate into report, clean orphan references\
**Ver A/B** — Citation accuracy + logical consistency checks\
**Step6** — Auto-fix verified issues\
**Step5** — Convert to narrative review article\
**Step7** — Generate summary table (population × metric) and Mermaid diagram

## Output

```
synthesize_output/
├── report.md          # Structured review report
├── article.md         # Narrative article
├── table.md           # Summary table (population × modality × coupling direction)
├── diagram.md         # Mermaid flowchart (render with: mmdc --input diagram.md --output diagram.svg)
├── verification.md    # Quality verification report
├── outline.json       # Auto-generated outline
├── findings/          # Per-paper finding JSONs (cached by paper+question hash)
└── cache/             # PDF text cache (SHA256)
```

## Stability

- **temperature=0** for all analytical steps (deterministic output)
- **Dual-layer caching**: PDF text + API findings; same paper + same question = instant reuse
- **Multi-key rotation**: auto-detects `DEEPSEEK_API_KEY_2`, `_3`, etc.
- **Orphan reference cleanup**: only papers actually cited in the body appear in the reference list
- **Cross-process rate limiting**: file-locked 1.5s interval for Semantic Scholar API calls

## Requirements

- Python 3.10+
- `pip install pymupdf openai requests`
- [Zotero](https://www.zotero.org/) with papers organized in collections
- [DeepSeek](https://platform.deepseek.com/) API key
- [Semantic Scholar](https://www.semanticscholar.org/product/api) API key (free, for `auto_lit.py`)
- [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (optional, for SVG rendering: `npm install -g @mermaid-js/mermaid-cli`)
