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

`protocol.yaml` defines the primary/secondary questions, scope, inclusion/exclusion criteria, configured screening reason codes, required synthesis sections, and `screening.enforcement` (`required`, `optional`, or `disabled`). Bundled formal templates use `required`; `disabled` is an explicit escape hatch for a manually assembled project with no screening record. Formal runs hash the protocol. When it changes, `protocol_changes.jsonl` records the old/new hash, timestamp, changed top-level fields, and optional reason.

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

Run all enabled searches or one path. A plan with neither enabled searches nor seed records fails instead of recording a zero-work success; `--allow-empty-search` is an explicit diagnostic override.

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

AI recommendations and confidence can be imported into their own columns; they never overwrite the human decision. Current CSV views, append-only history, and `prisma_counts.json` are regenerated deterministically. Review does not automatically perform human screening: an orchestrated run records `waiting_for_input` until decisions are imported.

For `screening.enforcement: required`, completeness is resolved against every record in `search/deduplicated_records.jsonl`, including seed records. A record is complete only when its latest title/abstract decision is `include`, `exclude`, or `duplicate`; title/abstract `include` records also require a latest full-text decision with one of those three values. Missing and `uncertain` decisions are not complete. The result is written to `screening/completeness.json` and is shared by eligibility, run-stage prerequisites, full-text status, and strict audit. `optional` and `disabled` projects still write the diagnostic artifact but do not impose that gate.

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

A publication can contain multiple studies; studies can contain multiple arms and outcomes. Extraction JSON accepts `source_record_id`, `source_file`, `publication`, and `studies`. Exact links are resolved in the order explicit record ID, DOI, PMID, normalized title, then explicit/manual file mapping. `record_publication_links.jsonl` and `study_record_links.jsonl` preserve the method, confidence, protocol hash, and timestamp. No fuzzy match is silently accepted.

`fulltext status` distinguishes `fulltext_available_record_ids` from `structured_extraction_available_record_ids` and reports their intersection. A publication/study link alone is not full text. Configure `fulltext.requirement` as `required` (the safe formal default), `structured_extraction_allowed` (manual structured extraction may stand in for a missing local full text), or `disabled`. In the second mode, included records still need structured extraction; evidence supplied without source text remains `unverified` unless a reviewer explicitly marks it `manual_verified`.

When screening is enforced, only a full-text `include` record with a study link is eligible downstream. Excluded, uncertain, duplicate, or unlinked studies cannot enter the matrix, contradiction analysis, memos, draft, or claim map. The `fulltext status` stage reports included records that still lack a bound PDF or structured extraction.

Outcome validation is separate from study-field validation. `outcome_schema` validates every actual `studies[].outcomes[]` item, including nested evidence. `effect_direction` records what changed; `support_relation` records whether that change supports, contradicts, is neutral toward, mixes, or is unclear for the configured claim. A domain-specific `beneficial_direction` may derive the relation. Legacy `direction` migrates only to `effect_direction`; it never implies support.

Outcome identity is configurable and does not need to depend on extraction order:

```yaml
outcome_identity:
  fields: [domain, measure, timepoint, population, comparison]
  fallback: domain_and_ordinal
```

An explicit `outcome_id` has highest priority, followed by `identity_key`, configured fields, and then the fallback. The ordinal fallback is retained for old schemas but writes `unstable_outcome_identity_fallback`, because reordering can change the identity. Top-level and nested configured fields are supported.

## Matrix, contradictions, and synthesis

```bash
review-assistant review matrix build --project ./my-review
review-assistant review matrix build --project ./my-review --row-mode study_comparison
review-assistant review evidence analyze --project ./my-review
review-assistant review synthesize --project ./my-review --model your-model
```

Matrix columns and contradiction dimensions come from `extraction_schema.yaml`. Missing data is not treated as a no-change result. Moderator differences are reported only as candidate explanations.

Review structure comes from protocol requirements plus `synthesis_plan.yaml` evidence filters. Filters compose explicit study/publication IDs, outcome domains, effect directions, support relations, study-field equality/membership, configured population/intervention scope, and contradiction membership. Required low-evidence sections remain present with `evidence_insufficient`; they never fall back to all studies. Review does not inherit Explore's per-section eight-finding cap.

Default Review synthesis calls the configured LLM with section specifications, protocol scope, outcomes, support relations, contradictions, missing data, qualifiers, and verified locations. It requires structured `section_text` plus claims; those claims are the primary source for `claim_map.json`. Every non-empty evidence section must map its substantive sentences and study citations to claims. Citation coverage scans known and unknown study-like tokens in the prose; an unknown token fails synthesis even when it is absent from the structured claims. The result is recorded in `synthesis/section_validation.json`; an invalid production writer result stops synthesis and returns an execution error. A section with no selected evidence is retained with deterministic evidence-insufficient text and never calls the writer. An injectable deterministic writer supports tests. `--offline-placeholder` is available only for scaffolding and visibly writes `PLACEHOLDER SYNTHESIS — NOT A REVIEW DRAFT`; strict audit rejects it.

Claims retain supporting and contradicting study IDs, scope status, population evidence levels, causal strength, qualifiers, and semantic flags. A study-level citation says which study is being discussed; a claim-level linkage must additionally name concrete `supporting_outcomes`/`contradicting_outcomes` and `supporting_evidence`/`contradicting_evidence` records. Each evidence location has a deterministic `evidence_id` derived from its study, outcome, quote, location fields, and ordinal. Unknown, excluded, outside-section, unresolved, or old study-only links are deliberately retained so audit can fail rather than hiding them. Strict audit indexes only active/current Outcome and Evidence records; historical references are reported as `superseded_outcome_reference`, `superseded_evidence_reference`, or `stale_extraction_reference` when identifiable. Every cited study on each side needs its own cited Outcome and Evidence, and every cited Outcome needs Evidence belonging to that Outcome; one Evidence location cannot cover a different Outcome. Supporting and contradicting sides receive the same study, outcome, evidence, eligibility, section, quote, and support-relation checks. Claim Outcomes are rechecked against the current section's outcome-domain, effect-direction, and support-relation filters even when their study is eligible. Revise the structured extraction or use an injected/human-reviewed structured writer result, then synthesize again; do not hand-edit only the prose and expect the claim map to follow.

## Audit and resumable runs

```bash
review-assistant review audit --project ./my-review
review-assistant review audit --project ./my-review --strict
review-assistant review run --project ./my-review --from-stage matrix --to-stage audit --resume
review-assistant review run --project ./my-review --dry-run
```

Quote status is `passed`, `unverified`, or `failed`. `passed` includes exact/normalized source verification and explicit manual verification; `unverified` means the quote has not been checked against source text; `failed` means verification did not find the quote. PDF extraction verifies exact or normalized full text (Unicode, ligatures, whitespace, punctuation, and line-break hyphenation); manual JSON is `unverified` unless full text is checked or `manual_verified: true` is explicit. A non-empty quote alone never passes. Strict audit treats critical unverified or failed evidence as an issue.

Audit checks unsupported/missing citations, unknown study citation tokens, section text/claim coverage, excluded/unlinked/outside-section citations, per-study and per-Outcome linkage coverage, unresolved or superseded Outcome/Evidence links, quote failures and critical unverified quotes, support-relation mismatches, section-filter violations, scope/adjacent leakage, population and causality flags, contradiction omission, required sections, protocol mismatch, duplicate counting, schema errors, unreported-field assertions, dropped/ineligible evidence, placeholder use, empty search plans, incomplete screening, missing full text/structured extraction, and unresolved citation keys. Strict exit codes are exact: passed audit `0`, audit issues `2`, execution error `1`.

Every orchestrated run stores metadata, stage status, input fingerprints, output fingerprints, and errors under `runs/<run-id>/`. Stages are `search`, `screen`, `fulltext`, `extract`, `matrix`, `analyze`, `synthesize`, and `audit`. Human prerequisites produce `waiting_for_input`; execution faults produce `failed`. `--resume` reuses the most recent incomplete run; `--force` reruns completed stages.

## Explore to Review bootstrap

```bash
review-assistant review bootstrap --from-explore ./explore-output --output ./formal-review
```

This writes `bootstrap_candidates.yaml` containing candidate questions, terms, scope concepts, sections, seed papers, and fields. The file is marked `unconfirmed`; nothing becomes a formal protocol until a reviewer edits and copies it into the formal configuration.

## Offline end-to-end tutorial

No network or paid API is needed for this path:

1. Initialize a generic project and add required sections to `protocol.yaml`.
2. Copy `examples/generic-fictional/extraction.json` into your working directory.
3. Import both screening stages, bind each extraction with `source_record_id`, and mark deliberately human-verified fixture quotes with `manual_verified: true`.
4. Run `review extract --input`, `matrix build`, `evidence analyze`, `synthesize --offline-fixture-writer` (test/tutorial fixture only), and `audit --strict`.
5. Inspect every JSONL/CSV/Markdown artifact and revise structured claims/extraction if needed.
6. Run `review run --from-stage matrix --resume` after a failed, waiting, or interrupted stage.

## Custom templates and review types

Add a directory containing the four required YAML files under `review_assistant/templates/`. Discovery is resource-based; the core engine does not need a new topic branch. New search sources are registered as runners, and new domain-specific extraction/audit behavior belongs in schema/protocol settings rather than Python keywords.

The two example directories demonstrate a fully fictional domain-neutral input and a topic-specific configuration. The latter is configuration only, not system behavior and not a completed systematic review.

## Human review and current boundaries

All AI screening recommendations, record links, extracted fields, quotes, support relations, contradiction groups, drafts, and semantic claim flags require human review. Re-ingesting a publication creates a new extraction run/version; matrix, contradiction analysis, evidence memos, claim resolution, and audit all use the current active entity view from `extraction/current_extraction_state.json`, while historical rows remain available for inspection and are marked `superseded` when replaced. Scope/population/causality analysis uses structured writer output and configured metadata; ambiguous cases remain `unclear`. A strict pass means the persisted workflow has no detected accounting, linkage, quote, configuration, or structural-audit issue; it is not a proof of scientific correctness or perfect natural-language entailment. The implementation exports PRISMA count data but does not draw a PRISMA diagram. Citation chasing is represented in the search plan but built-in automatic forward/backward chasing is not performed; add named searches or seed records explicitly. Structured JSON is the interchange format for manual extraction corrections.
