# Protocol-driven Review mode

Review Assistant has two intentionally different modes:

- **Explore** starts from a question and a supplied PDF/Zotero collection. It extracts findings, infers an outline, writes a narrative, and runs citation/logic checks. Its report is explicitly labelled as an exploratory narrative synthesis, not a systematic review.
- **Review** starts from an editable protocol. Search provenance, screening decisions, publication/study extraction, evidence matrices, contradictions, required sections, claims, and audit results are persisted as versioned artifacts.

Use Explore to orient yourself or develop a question. Use Review when eligibility, reproducibility, evidence accounting, and an auditable protocol matter. Existing module entry points such as `python -m review_assistant.explore_synthesize` remain supported; the unified equivalent is `review-assistant explore run -- <existing arguments>`.

## Initialize a project

```bash
review-assistant review init ./my-review --template generic-structured-review
```

Available bundled templates are `generic-structured-review`, `biomedical-intervention`, and `animal-intervention`. A project contains `project.yaml`, `protocol.yaml`, `search_plan.yaml`, `extraction_schema.yaml`, `synthesis_plan.yaml`, stage directories, and `runs/`.

`protocol.yaml` defines the primary/secondary questions, scope, inclusion/exclusion criteria, configured screening reason codes, and required synthesis sections. Formal runs hash it. When it changes, `protocol_changes.jsonl` records the old/new hash, timestamp, changed top-level fields, and optional reason.

`extraction_schema.yaml` supports string, number, integer, boolean, enum, list, and nested object fields. Each field may declare `required`, `default`, `missing_value`, `description`, `aliases`, `extraction_instruction`, `evidence_requirement`, and validation metadata. The engine interprets types and persistence only; domain meanings remain in this file.

## Search and screening

Define any number of named searches in `search_plan.yaml`:

```yaml
searches:
  - id: primary
    source: pubmed
    query: "replace with the exact reproducible query"
    enabled: true
  - id: supplementary
    source: semantic_scholar
    query: "replace with the supplementary query"
    enabled: false
```

Run all enabled searches or one path:

```bash
review-assistant review search --project ./my-review
review-assistant review search --project ./my-review --search-id primary
```

Raw results/logs are append-only. Deduplicated records retain every search provenance entry. Search failures are logged without erasing successful sources.

Screening is two-stage (`title_abstract`, `fulltext`) with `include`, `exclude`, `uncertain`, and `duplicate`. Import ordinary, ASReview, or Rayyan-like CSV exports by providing explicit mappings:

```bash
review-assistant review screen import --project ./my-review decisions.csv \
  --map record_id=id --map decision=decision --map reason_code=reason \
  --stage title_abstract --reviewer reviewer-name
```

AI recommendations and confidence can be imported into their own columns; they never overwrite the human decision. Current CSV views, append-only history, and `prisma_counts.json` are regenerated deterministically.

## Full text and study extraction

Place PDFs under `fulltext/` and inspect availability:

```bash
review-assistant review fulltext status --project ./my-review
review-assistant review extract --project ./my-review --fulltext-dir ./my-review/fulltext --model your-model
```

The extractor reuses shared PDF extraction, a schema-derived EvidencePack, the configured LLM provider, and local quote verification. It never fills unreported fields from general knowledge. Failures remain in `extraction_errors.jsonl`.

For human-reviewed or offline extraction, import the generic JSON shape directly:

```bash
review-assistant review extract --project ./my-review --input extraction.json
```

A publication can contain multiple studies; studies can contain multiple arms and outcomes. IDs are deterministic. Publication, study, and outcome JSONL exports remain separate and can be manually revised/re-imported.

## Matrix, contradictions, and synthesis

```bash
review-assistant review matrix build --project ./my-review
review-assistant review matrix build --project ./my-review --row-mode study_comparison
review-assistant review evidence analyze --project ./my-review
review-assistant review synthesize --project ./my-review
```

Matrix columns and contradiction dimensions come from `extraction_schema.yaml`. Missing data is not treated as a no-change result. Moderator differences are reported only as candidate explanations.

Review structure comes from protocol requirements plus configured evidence mappings. Required low-evidence sections remain present. All eligible studies are divided by configurable memo batch size; Review does not inherit Explore's per-section eight-finding cap. The section writer accepts either Explore findings or Review evidence memos through a mode-aware interface.

Synthesis produces resolved plans, evidence memos, section drafts, `review_draft.md`, and `claim_map.json`. Claims link to supporting/contradicting studies and quote locations and receive stable IDs.

## Audit and resumable runs

```bash
review-assistant review audit --project ./my-review
review-assistant review audit --project ./my-review --strict
review-assistant review run --project ./my-review --from-stage matrix --to-stage audit --resume
review-assistant review run --project ./my-review --dry-run
```

Audit checks unsupported/missing citations, quote failures, scope/adjacent leakage, configured population and causality flags, contradiction omission, missing protocol sections, protocol mismatch, duplicate counting, unreported-field assertions, dropped evidence, and unresolved citation keys. Normal mode emits outputs even with issues; strict mode returns exit code 2.

Every orchestrated run stores metadata, stage status, input fingerprints, output fingerprints, and errors under `runs/<run-id>/`. Missing screening or extraction prerequisites produce an actionable error. `--resume` reuses the most recent incomplete run; `--force` reruns completed stages.

## Explore to Review bootstrap

```bash
review-assistant review bootstrap --from-explore ./explore-output --output ./formal-review
```

This writes `bootstrap_candidates.yaml` containing candidate questions, terms, scope concepts, sections, seed papers, and fields. The file is marked `unconfirmed`; nothing becomes a formal protocol until a reviewer edits and copies it into the formal configuration.

## Offline end-to-end tutorial

No network or paid API is needed for this path:

1. Initialize a generic project and add required sections to `protocol.yaml`.
2. Copy `examples/generic-fictional/extraction.json` into your working directory.
3. Run `review extract --input`, `matrix build`, `evidence analyze`, `synthesize`, and `audit` as shown above.
4. Inspect every JSONL/CSV/Markdown artifact and revise structured extraction if needed.
5. Run `review run --from-stage matrix --resume` after a failed or interrupted stage.

## Custom templates and review types

Add a directory containing the four required YAML files under `review_assistant/templates/`. Discovery is resource-based; the core engine does not need a new topic branch. New search sources are registered as runners, and new domain-specific extraction/audit behavior belongs in schema/protocol settings rather than Python keywords.

The two example directories demonstrate a fully fictional domain-neutral input and a topic-specific configuration. The latter is configuration only, not system behavior and not a completed systematic review.

## Human review and current boundaries

All AI screening recommendations, extracted fields, quotes, contradiction groups, drafts, and claim audits require human review. The implementation exports PRISMA count data but does not draw a PRISMA diagram. Citation chasing is represented in the search plan but built-in automatic forward/backward chasing is not performed; add named searches or seed records explicitly. Structured JSON is the interchange format for manual extraction corrections.
