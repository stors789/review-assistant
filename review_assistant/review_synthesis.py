"""Protocol-driven synthesis plans, lossless evidence memos, and claim maps."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .io_utils import atomic_write_text, load_yaml, read_jsonl, stable_id, write_json
from .project import ReviewProject
from .eligibility import resolve_eligible_studies
from .config import DEFAULT_PRO_MODEL, get_model
from .studies import evidence_location_id


def eligible_study_ids(project: ReviewProject) -> list[str]:
    """Backward-compatible public alias for the shared eligibility resolver."""
    return resolve_eligible_studies(project)


def resolve_synthesis_plan(project: ReviewProject) -> dict[str, Any]:
    configured = load_yaml(project.root / "synthesis_plan.yaml")
    protocol = project.protocol.data
    configured_sections = configured.get("sections", [])
    by_title = {str(item.get("title", "")): item for item in configured_sections if isinstance(item, dict)}
    required = protocol.get("synthesis", {}).get("required_sections", [])
    section_specs: list[dict[str, Any]] = []
    for index, raw in enumerate(required):
        item = {"title": raw} if isinstance(raw, str) else dict(raw)
        item = {**by_title.get(str(item.get("title", "")), {}), **item}
        item.setdefault("section_id", f"S{index + 1:02d}")
        section_specs.append(item)
    known_titles = {item.get("title") for item in section_specs}
    for raw in configured_sections:
        if raw.get("title") not in known_titles:
            item = dict(raw)
            item.setdefault("section_id", f"S{len(section_specs) + 1:02d}")
            section_specs.append(item)
    all_studies = eligible_study_ids(project)
    study_rows = {item["study_id"]: item for item in read_jsonl(project.root / "extraction" / "studies.jsonl") if item.get("study_id") in all_studies}
    outcomes = read_jsonl(project.root / "extraction" / "outcomes.jsonl")
    outcomes_by_study: dict[str, list[dict[str, Any]]] = defaultdict(list)
    directions: dict[str, list[str]] = defaultdict(list)
    relations: dict[str, list[str]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.get("study_id") not in study_rows:
            continue
        outcomes_by_study[outcome["study_id"]].append(outcome)
        directions[outcome["study_id"]].append(outcome.get("effect_direction", outcome.get("direction", "unclear")))
        relations[outcome["study_id"]].append(outcome.get("support_relation", "unclear"))
    contradiction_ids: set[str] = set()
    contradiction_path = project.root / "evidence" / "contradiction_groups.json"
    if contradiction_path.exists():
        payload = json.loads(contradiction_path.read_text(encoding="utf-8"))
        contradiction_ids = {
            str(sid) for group in payload.get("groups", []) if group.get("has_directional_inconsistency")
            for sid in group.get("study_ids", [])
        }
    resolved = []
    for spec in section_specs:
        evidence_filter = dict(spec.get("evidence_filter", {}))
        if "included_study_ids" in spec and "study_ids" not in evidence_filter:
            evidence_filter["study_ids"] = list(spec.get("included_study_ids", []))
        study_ids, explanations = _select_section_studies(
            all_studies, study_rows, outcomes_by_study, evidence_filter, contradiction_ids,
        )
        relation_sets = {name: sorted(sid for sid in study_ids if name in relations.get(sid, [])) for name in ("supports", "contradicts", "neutral", "mixed", "unclear")}
        resolved.append({
            "section_id": spec["section_id"], "title": str(spec.get("title", "")),
            "required_questions": list(spec.get("required_questions", protocol.get("synthesis", {}).get("required_questions", []))),
            "selection_rule": evidence_filter,
            "included_study_ids": study_ids, "supporting_evidence": relation_sets["supports"],
            "contradicting_evidence": relation_sets["contradicts"], "neutral_evidence": relation_sets["neutral"],
            "mixed_evidence": relation_sets["mixed"], "unclear_evidence": relation_sets["unclear"],
            "effect_directions": {name: sorted(sid for sid in study_ids if name in directions.get(sid, [])) for name in ("increase", "decrease", "no_change", "mixed", "unclear")},
            "required_qualifiers": list(spec.get("required_qualifiers", [])),
            "excluded_study_ids": sorted(set(all_studies) - set(study_ids)),
            "selection_explanations": explanations,
            "missing_evidence": [] if study_ids else ["evidence_insufficient"],
            "evidence_memo_dependencies": [],
        })
    result = {"schema_version": "1.0", "protocol_hash": project.track_protocol(), "sections": resolved, "settings": configured.get("settings", {})}
    write_json(project.root / "synthesis" / "resolved_synthesis_plan.json", result)
    return result


def _select_section_studies(
    eligible_ids: list[str], studies: dict[str, dict[str, Any]],
    outcomes: dict[str, list[dict[str, Any]]], rule: dict[str, Any], contradiction_ids: set[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Apply domain-neutral, composable section evidence filters with explanations."""
    explanations: dict[str, list[str]] = {}
    if rule.get("no_evidence") is True or not rule:
        reason = "no_evidence_requested" if rule.get("no_evidence") is True else "no_selection_rule"
        return [], {sid: [reason] for sid in eligible_ids}
    selected: list[str] = []
    explicit = set(str(value) for value in rule.get("study_ids", []))
    publications = set(str(value) for value in rule.get("publication_ids", []))
    domains = set(str(value) for value in rule.get("outcome_domains", []))
    effects = set(str(value) for value in rule.get("effect_directions", []))
    relations = set(str(value) for value in rule.get("support_relations", []))
    equals = rule.get("study_field_equals", {}) if isinstance(rule.get("study_field_equals", {}), dict) else {}
    membership = rule.get("study_field_in", {}) if isinstance(rule.get("study_field_in", {}), dict) else {}
    population_field = str(rule.get("population_field", "population.scope"))
    intervention_field = str(rule.get("intervention_field", "intervention.scope"))
    for sid in eligible_ids:
        study = studies[sid]
        study_outcomes = outcomes.get(sid, [])
        reasons: list[str] = []
        if explicit and sid not in explicit:
            reasons.append("study_id_not_selected")
        if publications and str(study.get("publication_id")) not in publications:
            reasons.append("publication_id_not_selected")
        if domains and not domains.intersection(str(item.get("domain")) for item in study_outcomes):
            reasons.append("outcome_domain_not_matched")
        if effects and not effects.intersection(str(item.get("effect_direction", item.get("direction", "unclear"))) for item in study_outcomes):
            reasons.append("effect_direction_not_matched")
        if relations and not relations.intersection(str(item.get("support_relation", "unclear")) for item in study_outcomes):
            reasons.append("support_relation_not_matched")
        context = {**study.get("fields", {}), **study}
        for field_name, expected in equals.items():
            if _nested_value(context, str(field_name)) != expected:
                reasons.append(f"study_field_not_equal:{field_name}")
        for field_name, allowed in membership.items():
            allowed_values = allowed if isinstance(allowed, list) else [allowed]
            actual = _nested_value(context, str(field_name))
            actual_values = actual if isinstance(actual, list) else [actual]
            if not set(actual_values).intersection(allowed_values):
                reasons.append(f"study_field_not_in:{field_name}")
        if "population_scope" in rule and _nested_value(context, population_field) != rule["population_scope"]:
            reasons.append("population_scope_not_matched")
        if "intervention_scope" in rule and _nested_value(context, intervention_field) != rule["intervention_scope"]:
            reasons.append("intervention_scope_not_matched")
        if rule.get("contradiction_only") is True and sid not in contradiction_ids:
            reasons.append("not_in_contradiction_group")
        if rule.get("include_all_studies") is not True and not any(
            key in rule for key in (
                "study_ids", "publication_ids", "outcome_domains", "effect_directions",
                "support_relations", "study_field_equals", "study_field_in", "population_scope",
                "intervention_scope", "contradiction_only",
            )
        ):
            reasons.append("no_supported_filter")
        explanations[sid] = reasons or ["included_by_rule"]
        if not reasons:
            selected.append(sid)
    return sorted(selected), explanations


def _nested_value(value: Any, path: str, default: Any = "not_reported") -> Any:
    if isinstance(value, dict) and path in value:
        return value[path]
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def build_evidence_memos(project: ReviewProject, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    plan = plan or resolve_synthesis_plan(project)
    studies = {item["study_id"]: item for item in read_jsonl(project.root / "extraction" / "studies.jsonl")}
    outcomes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in read_jsonl(project.root / "extraction" / "outcomes.jsonl"):
        outcomes[item["study_id"]].append(item)
    batch_size = int(plan.get("settings", {}).get("evidence_batch_size", 25))
    if batch_size < 1:
        raise ValueError("evidence_batch_size must be positive")
    memo_dir = project.root / "evidence" / "evidence_memos"
    memo_dir.mkdir(parents=True, exist_ok=True)
    all_memos = []
    for section in plan["sections"]:
        ids = section["included_study_ids"]
        dependencies = []
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset:offset + batch_size]
            memo_id = f"{section['section_id']}_batch_{offset // batch_size + 1:03d}"
            items = [{"study_id": sid, "publication_id": studies[sid]["publication_id"], "fields": studies[sid].get("fields", {}), "outcomes": outcomes.get(sid, [])} for sid in batch]
            memo = {"schema_version": "1.0", "memo_id": memo_id, "section_id": section["section_id"], "study_ids": batch, "evidence_items": items}
            write_json(memo_dir / f"{memo_id}.json", memo)
            lines = [f"# Evidence memo {memo_id}", "", f"Studies: {', '.join(batch)}", ""]
            for item in items:
                lines.extend([f"## {item['study_id']}", "", f"Publication: {item['publication_id']}", f"Outcomes: {json.dumps(item['outcomes'], ensure_ascii=False, sort_keys=True)}", ""])
            atomic_write_text(memo_dir / f"{memo_id}.md", "\n".join(lines))
            dependencies.append(memo_id)
            all_memos.append(memo)
        section["evidence_memo_dependencies"] = dependencies
        write_json(memo_dir / f"{section['section_id']}_summary.json", {"schema_version": "1.0", "section_id": section["section_id"], "memo_ids": dependencies, "all_study_ids": ids, "evidence_count": len(ids)})
    write_json(project.root / "synthesis" / "resolved_synthesis_plan.json", plan)
    return all_memos


PLACEHOLDER_BANNER = "PLACEHOLDER SYNTHESIS — NOT A REVIEW DRAFT"
SECTION_VALIDATION_SCHEMA_VERSION = "1.0"


def write_section(section_spec: dict[str, Any], evidence_bundle: list[dict[str, Any]], synthesis_context: dict[str, Any], mode: str, writer: Callable[..., Any] | None = None) -> dict[str, Any]:
    if mode not in {"explore", "review"}:
        raise ValueError("mode must be explore or review")

    # Evidence insufficiency is a deterministic protocol state.  Check it
    # before touching the writer so an LLM cannot turn an empty evidence bundle
    # into an unsupported scientific conclusion.
    if mode == "review" and not section_spec.get("included_study_ids"):
        text = str(
            section_spec.get("insufficient_evidence_text")
            or synthesis_context.get("insufficient_evidence_text")
            or "Evidence is insufficient for this protocol-required section."
        )
        result = {
            "section_text": text,
            "claims": [],
            "evidence_status": "insufficient",
            "writer_called": False,
            "writer_type": "none",
        }
        result["section_validation"] = validate_structured_section_output(section_spec, result, [])
        return result

    if writer:
        result = writer(section_spec=section_spec, evidence_bundle=evidence_bundle, synthesis_context=synthesis_context, mode=mode)
        if isinstance(result, str):
            normalized_result = {
                "section_text": result,
                "claims": [],
                "compatibility_fallback": True,
                "evidence_status": "available",
                "writer_called": True,
                "writer_type": _writer_type(writer),
            }
            normalized_result["section_validation"] = validate_structured_section_output(
                section_spec, normalized_result, section_spec.get("included_study_ids", []),
            )
            return normalized_result
        if not isinstance(result, dict) or not isinstance(result.get("section_text"), str) or not isinstance(result.get("claims"), list):
            raise ValueError("Review writer must return an object with section_text and claims")
        normalized_result = {
            **result,
            "evidence_status": result.get("evidence_status", "available"),
            "writer_called": True,
            "writer_type": result.get("writer_type", _writer_type(writer)),
        }
        normalized_result["section_validation"] = validate_structured_section_output(
            section_spec, normalized_result, section_spec.get("included_study_ids", []),
        )
        return normalized_result
    raise RuntimeError("Review synthesis requires a configured LLM writer or an explicit offline mode")


def _writer_type(writer: Callable[..., Any]) -> str:
    if isinstance(writer, ReviewLLMWriter):
        return "llm"
    name = getattr(writer, "__name__", "")
    if name == "fixture_writer":
        return "fixture"
    if name == "_placeholder_writer":
        return "placeholder"
    return "injected"


def _normalise_coverage_text(value: str) -> str:
    value = re.sub(r"[`*_]", "", str(value))
    value = re.sub(r"\s+", " ", value, flags=re.UNICODE)
    return value.strip().casefold()


def _as_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _claim_study_ids(claim: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "supporting_study_ids", "supporting_studies", "contradicting_study_ids",
        "contradicting_studies", "study_ids",
    ):
        values.extend(_as_id_list(claim.get(key)))
    for key in ("citation", "citations", "citation_ids"):
        value = claim.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, list):
            values.extend(str(item).strip() for item in value if str(item).strip())
    return list(dict.fromkeys(values))


def _extract_study_citations(text: str, known_ids: set[str]) -> list[str]:
    """Find known IDs in prose and bracket citations without topic heuristics."""
    citations: list[str] = []
    for study_id in sorted(known_ids, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(study_id)}(?![A-Za-z0-9])", text):
            citations.append(study_id)
    return citations


def _split_section_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in str(text).splitlines():
        line = re.sub(r"^\s*(?:[-*+]\s+|>\s+|#{1,6}\s+)", "", line).strip()
        if not line:
            continue
        pieces = re.split(r"(?<=[.!?。！？])\s+", line)
        sentences.extend(piece.strip() for piece in pieces if piece.strip())
    return sentences


def validate_structured_section_output(
    section_spec: dict[str, Any], writer_result: Any, eligible_study_ids: Iterable[str], *, raise_on_failure: bool = False,
) -> dict[str, Any]:
    """Validate that section prose and structured claims cover one another.

    The check is intentionally conservative rather than a natural-language
    entailment system: every non-structural sentence must map to a claim, and
    every study citation must be represented by a claim.  Callers may request
    an exception, while the default return value remains useful for audit
    artifacts and diagnostics.
    """
    allowed_ids = {str(value) for value in eligible_study_ids if str(value)}
    section_ids = {str(value) for value in section_spec.get("included_study_ids", allowed_ids) if str(value)}
    evidence_available = bool(section_ids)
    metadata: dict[str, Any] = {
        "schema_version": SECTION_VALIDATION_SCHEMA_VERSION,
        "section_id": str(section_spec.get("section_id", "")),
        "claim_count": 0,
        "citation_count": 0,
        "mapped_citation_count": 0,
        "unmapped_citations": [],
        "unmapped_sentences": [],
        "errors": [],
        "coverage_status": "passed",
    }
    errors: list[str] = metadata["errors"]

    if not isinstance(writer_result, dict):
        errors.append("writer_result_not_object")
        writer_result = {}
    section_text = writer_result.get("section_text")
    claims = writer_result.get("claims")
    if not isinstance(section_text, str):
        errors.append("section_text_not_string")
        section_text = ""
    if not isinstance(claims, list):
        errors.append("claims_not_list")
        claims = []

    metadata["claim_count"] = len(claims)
    if evidence_available and not claims:
        errors.append("nonempty_section_zero_claims")
    if not evidence_available and claims:
        errors.append("empty_evidence_section_has_claims")

    normalised_section = _normalise_coverage_text(section_text)
    valid_claims: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claim_{index}_not_object")
            continue
        sentence = str(claim.get("sentence", "")).strip()
        if not sentence:
            errors.append(f"claim_{index}_sentence_empty")
            continue
        valid_claims.append(claim)
        if _normalise_coverage_text(sentence) not in normalised_section:
            errors.append(f"claim_{index}_sentence_absent_from_section")
        ids = _claim_study_ids(claim)
        claim_ids.update(ids)
        citation_blob = json.dumps(
            {key: claim.get(key) for key in ("citation", "citations", "citation_ids")},
            ensure_ascii=False,
        )
        for study_id in ids:
            if study_id not in sentence and study_id not in citation_blob:
                errors.append(f"claim_{index}_citation_not_in_sentence_or_citation_field:{study_id}")
            if study_id not in section_ids:
                errors.append(f"claim_{index}_study_outside_section:{study_id}")
            elif study_id not in allowed_ids:
                errors.append(f"claim_{index}_study_not_eligible:{study_id}")

    citations = _extract_study_citations(section_text, allowed_ids | section_ids)
    metadata["citation_count"] = len(citations)
    unmapped = sorted(set(citations) - claim_ids)
    metadata["unmapped_citations"] = unmapped
    metadata["mapped_citation_count"] = len(citations) - len(unmapped)
    if unmapped:
        errors.append("section_citation_not_in_claim_map")
    if evidence_available and not citations:
        errors.append("evidence_section_has_no_study_citation")

    configured_patterns = section_spec.get("non_substantive_sentence_patterns", [])
    patterns = [re.compile(str(pattern), re.IGNORECASE) for pattern in configured_patterns if str(pattern)] if isinstance(configured_patterns, list) else []
    insufficient_text = _normalise_coverage_text(
        str(section_spec.get("insufficient_evidence_text") or "Evidence is insufficient for this protocol-required section.")
    )
    unmapped_sentences: list[str] = []
    claim_sentences = [_normalise_coverage_text(str(claim.get("sentence", ""))) for claim in valid_claims]
    for sentence in _split_section_sentences(section_text):
        normalised = _normalise_coverage_text(sentence)
        if not normalised or normalised == insufficient_text or any(pattern.search(sentence) for pattern in patterns):
            continue
        if not any(normalised in claim_sentence or claim_sentence in normalised for claim_sentence in claim_sentences):
            unmapped_sentences.append(sentence)
    metadata["unmapped_sentences"] = unmapped_sentences
    if unmapped_sentences:
        errors.append("substantive_sentence_not_mapped")

    if errors:
        metadata["coverage_status"] = "failed"
    if raise_on_failure and errors:
        raise ValueError(
            f"Invalid structured output for section {metadata['section_id'] or '<unknown>'}: "
            + "; ".join(dict.fromkeys(errors))
        )
    return metadata


def synthesize_review(
    project: ReviewProject, writer: Callable[..., Any] | None = None, *,
    model: str | None = None, offline_placeholder: bool = False,
) -> str:
    plan = resolve_synthesis_plan(project)
    memos = build_evidence_memos(project, plan)
    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for memo in memos:
        by_section[memo["section_id"]].append(memo)
    protocol = project.protocol.data
    selected_model = model or get_model(DEFAULT_PRO_MODEL)
    if writer is None and not offline_placeholder:
        writer = ReviewLLMWriter(selected_model)
    if offline_placeholder:
        writer = _placeholder_writer
    title = protocol["review"].get("title") or "Review synthesis"
    sections = [f"# {title}", "", f"Protocol hash: `{plan['protocol_hash']}`", ""]
    if offline_placeholder:
        sections.extend([f"> **{PLACEHOLDER_BANNER}**", ""])
    draft_dir = project.root / "synthesis" / "section_drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    structured_sections: list[dict[str, Any]] = []
    section_validations: list[dict[str, Any]] = []
    for spec in plan["sections"]:
        result = write_section(spec, by_section.get(spec["section_id"], []), {"protocol": protocol, "model": selected_model}, "review", writer)
        content = result["section_text"]
        structured_sections.append({"section_id": spec["section_id"], **result})
        section_validations.append(dict(result.get("section_validation", {})))
        atomic_write_text(draft_dir / f"{spec['section_id']}.md", content + "\n")
        sections.extend([f"## {spec['title']}", "", content, ""])
        if result.get("section_validation", {}).get("coverage_status") == "failed" and not offline_placeholder:
            validation_payload = {
                "schema_version": SECTION_VALIDATION_SCHEMA_VERSION,
                "protocol_hash": plan["protocol_hash"],
                "status": "failed",
                "sections": section_validations,
            }
            write_json(project.root / "synthesis" / "section_validation.json", validation_payload)
            write_json(project.root / "synthesis" / "claim_map.json", {
                "schema_version": "1.1", "protocol_hash": plan["protocol_hash"],
                "status": "invalid", "claims": [],
                "section_validation_errors": section_validations,
            })
            raise ValueError(f"Structured synthesis coverage failed for section {spec['section_id']}")
    report = "\n".join(sections).rstrip() + "\n"
    atomic_write_text(project.root / "synthesis" / "review_draft.md", report)
    validation_status = "failed" if any(item.get("coverage_status") == "failed" for item in section_validations) else "passed"
    write_json(project.root / "synthesis" / "section_validation.json", {
        "schema_version": SECTION_VALIDATION_SCHEMA_VERSION,
        "protocol_hash": plan["protocol_hash"], "status": validation_status,
        "sections": section_validations,
    })
    build_claim_map(project, report, plan, structured_sections)
    write_json(project.root / "synthesis" / "synthesis_metadata.json", {
        "schema_version": "1.0", "protocol_hash": plan["protocol_hash"], "model": selected_model,
        "writer": "offline_placeholder" if offline_placeholder else ("injected" if not isinstance(writer, ReviewLLMWriter) else "llm"),
        "placeholder": offline_placeholder, "section_validation_status": validation_status,
    })
    return report


def build_claim_map(project: ReviewProject, report: str, plan: dict[str, Any] | None = None, structured_sections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    plan = plan or resolve_synthesis_plan(project)
    valid_studies = set(eligible_study_ids(project))
    outcomes = read_jsonl(project.root / "extraction" / "outcomes.jsonl")
    outcomes_by_id = {
        str(outcome["outcome_id"]): outcome
        for outcome in outcomes
        if outcome.get("outcome_id")
    }
    evidence_by_id: dict[str, dict[str, Any]] = {}
    locations = defaultdict(list)
    for outcome in outcomes:
        outcome_id_value = str(outcome.get("outcome_id", ""))
        study_id_value = str(outcome.get("study_id", ""))
        for ordinal, raw_location in enumerate(outcome.get("evidence", [])):
            location = dict(raw_location)
            location["evidence_id"] = evidence_location_id(study_id_value, outcome_id_value, location, ordinal)
            location["study_id"] = study_id_value
            location["outcome_id"] = outcome_id_value
            evidence_by_id[location["evidence_id"]] = location
            locations[study_id_value].append(location)
    contradiction_payload = project.root / "evidence" / "contradiction_groups.json"
    contradiction_groups = json.loads(contradiction_payload.read_text())["groups"] if contradiction_payload.exists() else []
    claims = []
    for section in structured_sections or []:
        section_id = str(section.get("section_id", ""))
        spec = next((item for item in plan["sections"] if item["section_id"] == section_id), {})
        for raw in section.get("claims", []):
            if not isinstance(raw, dict) or not str(raw.get("sentence", "")).strip():
                continue
            claims.append(_analyze_claim(
                project, raw, section_id, spec, valid_studies, locations,
                outcomes_by_id, evidence_by_id, contradiction_groups,
            ))
    result = {"schema_version": "1.0", "protocol_hash": project.track_protocol(), "claims": claims}
    write_json(project.root / "synthesis" / "claim_map.json", result)
    return result


class ReviewLLMWriter:
    def __init__(self, model: str):
        self.model = model

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        from . import llm_client
        section = kwargs["section_spec"]
        context = kwargs["synthesis_context"]
        evidence = kwargs["evidence_bundle"]
        system = (
            "You are a rigorous evidence-synthesis writer. Use only supplied eligible studies. "
            "Return JSON with section_text and claims. Every substantive claim must cite study IDs and preserve "
            "supporting, contradicting, neutral, mixed, missing, population, scope, causal-strength, and qualifier distinctions. "
            "Do not invent numbers or quotations; do not convert unreported information into no effect; do not overgeneralize populations or causality."
        )
        user = json.dumps({
            "section_spec": section, "protocol_scope": context.get("protocol", {}).get("scope", {}),
            "review_question": context.get("protocol", {}).get("review", {}).get("primary_question", ""),
            "evidence_memos": evidence,
            "output_schema": {
                "section_text": "markdown text with [study_id] citations",
                "claims": [{
                    "sentence": "claim sentence", "supporting_study_ids": [], "contradicting_study_ids": [],
                    "supporting_outcome_ids": [], "contradicting_outcome_ids": [],
                    "supporting_evidence_refs": [], "contradicting_evidence_refs": [],
                    "required_qualifiers": [], "scope_status": "inside|adjacent|outside|unclear",
                    "population_levels": [], "claimed_population_levels": [],
                    "causal_strength": "descriptive|associational|causal|unclear",
                    "animal_to_human": False, "population_overgeneralization": False,
                    "causal_inflation": False, "asserted_fields": [], "unreported_field_claims": [],
                }],
            },
        }, ensure_ascii=False, indent=2)
        result = llm_client.call_json(llm_client.get_client(), system, user, self.model, max_tokens=16384)
        if not isinstance(result, dict):
            raise ValueError("Review LLM writer returned a non-object response")
        return result


def _placeholder_writer(**kwargs: Any) -> dict[str, Any]:
    return {"section_text": PLACEHOLDER_BANNER, "claims": []}


def fixture_writer(**kwargs: Any) -> dict[str, Any]:
    """Deterministic structured writer for offline integration tests and tutorials."""
    section = kwargs["section_spec"]
    items = [item for memo in kwargs["evidence_bundle"] for item in memo.get("evidence_items", [])]
    if not items:
        return {"section_text": "Evidence is insufficient for this protocol-required section.", "claims": []}
    supporting_studies: list[str] = []
    contradicting_studies: list[str] = []
    supporting_outcomes: list[str] = []
    contradicting_outcomes: list[str] = []
    supporting_evidence: list[str] = []
    contradicting_evidence: list[str] = []
    cited_studies: list[str] = []
    for item in items:
        study_id_value = str(item["study_id"])
        selected = next(
            (
                (outcome, evidence)
                for outcome in item.get("outcomes", [])
                for evidence in outcome.get("evidence", [])
                if outcome.get("outcome_id") and evidence.get("evidence_id")
            ),
            None,
        )
        if selected is None:
            continue
        outcome, evidence = selected
        outcome_id_value = str(outcome["outcome_id"])
        evidence_id_value = str(evidence["evidence_id"])
        relation = str(outcome.get("support_relation", "unclear"))
        if relation == "contradicts":
            contradicting_studies.append(study_id_value)
            contradicting_outcomes.append(outcome_id_value)
            contradicting_evidence.append(evidence_id_value)
        elif relation == "supports":
            supporting_studies.append(study_id_value)
            supporting_outcomes.append(outcome_id_value)
            supporting_evidence.append(evidence_id_value)
        else:
            # The fixture keeps neutral/unclear evidence visible in the claim
            # map so the symmetric audit can require an explicit explanation.
            supporting_studies.append(study_id_value)
            supporting_outcomes.append(outcome_id_value)
            supporting_evidence.append(evidence_id_value)
        cited_studies.append(study_id_value)
    cited_studies = list(dict.fromkeys(cited_studies))
    sentence = f"Eligible evidence for this section was reported by {len(cited_studies)} study record(s) " + " ".join(f"[{sid}]" for sid in cited_studies) + "."
    support_level = "mixed" if supporting_studies and contradicting_studies else ("supported" if supporting_studies else "unsupported")
    return {"section_text": sentence, "claims": [{
        "sentence": sentence, "supporting_study_ids": supporting_studies, "contradicting_study_ids": contradicting_studies,
        "supporting_outcome_ids": supporting_outcomes, "contradicting_outcome_ids": contradicting_outcomes,
        "supporting_evidence_refs": supporting_evidence, "contradicting_evidence_refs": contradicting_evidence,
        "support_level": support_level,
        "required_qualifiers": list(section.get("required_qualifiers", [])), "scope_status": "unclear",
        "population_levels": [], "causal_strength": "descriptive",
    }]}


def _reference_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _resolve_evidence_references(refs: list[Any], evidence_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Any]]:
    resolved: list[dict[str, Any]] = []
    unresolved: list[Any] = []
    for reference in refs:
        if isinstance(reference, str):
            evidence_id_value = reference.strip()
        elif isinstance(reference, dict):
            evidence_id_value = str(reference.get("evidence_id", "")).strip()
        else:
            evidence_id_value = ""
        evidence = evidence_by_id.get(evidence_id_value)
        if not evidence:
            unresolved.append(reference)
            continue
        resolved.append(dict(evidence))
    return resolved, unresolved


def _analyze_claim(
    project: ReviewProject, raw: dict[str, Any], section_id: str, section_spec: dict[str, Any],
    valid_studies: set[str], locations: dict[str, list[dict[str, Any]]],
    outcomes_by_id: dict[str, dict[str, Any]], evidence_by_id: dict[str, dict[str, Any]],
    contradiction_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    sentence = str(raw.get("sentence", "")).strip()
    supporting = _as_id_list(raw.get("supporting_study_ids", raw.get("supporting_studies", [])))
    contradicting = _as_id_list(raw.get("contradicting_study_ids", raw.get("contradicting_studies", [])))
    supporting_outcomes = _as_id_list(raw.get("supporting_outcome_ids", raw.get("supporting_outcomes", [])))
    contradicting_outcomes = _as_id_list(raw.get("contradicting_outcome_ids", raw.get("contradicting_outcomes", [])))
    supporting_refs = _reference_list(raw.get("supporting_evidence_refs", []))
    contradicting_refs = _reference_list(raw.get("contradicting_evidence_refs", []))
    supporting_evidence, unresolved_supporting_evidence = _resolve_evidence_references(supporting_refs, evidence_by_id)
    contradicting_evidence, unresolved_contradicting_evidence = _resolve_evidence_references(contradicting_refs, evidence_by_id)
    required = list(raw.get("required_qualifiers", section_spec.get("required_qualifiers", [])))
    study_rows = {
        str(item.get("study_id")): item for item in read_jsonl(project.root / "extraction" / "studies.jsonl")
        if item.get("study_id")
    }
    supporting_rows = [study_rows[sid] for sid in supporting if sid in study_rows]
    scope = str(raw.get("scope_status", raw.get("protocol_scope_status", "unclear")))
    if scope not in {"inside", "adjacent", "outside", "unclear"}:
        scope = "unclear"
    if scope == "unclear":
        scope = _infer_claim_scope(project, supporting_rows)
    known_support = [sid for sid in supporting if sid in valid_studies]
    omitted_contradictions = sorted({
        sid for group in contradiction_groups if group.get("has_directional_inconsistency")
        and set(known_support).intersection(group.get("study_ids", []))
        for sid in group.get("study_ids", []) if sid not in supporting + contradicting
    })
    support_level = str(raw.get("support_level") or ("unsupported" if not supporting else ("mixed" if contradicting else "supported")))
    population_levels = list(raw.get("population_levels", []))
    if not population_levels:
        population_levels = sorted({
            str(value) for study in supporting_rows
            if (value := _claim_field(study, "population.evidence_level", _claim_field(study, "population.level", "")))
        })
    claimed_population_levels = list(raw.get("claimed_population_levels", []))
    population_overgeneralization = bool(raw.get("population_overgeneralization", False))
    animal_to_human = bool(raw.get("animal_to_human", False))
    analysis_config = project.protocol.data.get("claim_analysis", {})
    hierarchy = list(analysis_config.get("population_hierarchy", [])) if isinstance(analysis_config, dict) else []
    if hierarchy and population_levels and claimed_population_levels:
        ranks = {str(value): index for index, value in enumerate(hierarchy)}
        evidence_ranks = [ranks[value] for value in population_levels if value in ranks]
        claim_ranks = [ranks[value] for value in claimed_population_levels if value in ranks]
        if evidence_ranks and claim_ranks and max(claim_ranks) > max(evidence_ranks):
            population_overgeneralization = True
    for pair in analysis_config.get("cross_population_pairs", []) if isinstance(analysis_config, dict) else []:
        if isinstance(pair, dict) and pair.get("evidence") in population_levels and pair.get("claim") in claimed_population_levels:
            animal_to_human = True
    causal_strength = str(raw.get("causal_strength", "unclear"))
    known_study_strengths = {
        str(value) for study in supporting_rows
        if (value := _claim_field(study, "study.causal_strength", "")) in {"descriptive", "associational", "causal"}
    }
    causal_inflation = bool(raw.get("causal_inflation", False)) or (
        causal_strength == "causal" and bool(known_study_strengths) and known_study_strengths != {"causal"}
    )
    asserted_fields = [str(value) for value in raw.get("asserted_fields", [])]
    derived_unreported = [
        field for field in asserted_fields if supporting_rows
        and all(_claim_field(study, field, "not_reported") in {None, "", "not_reported"} for study in supporting_rows)
    ]
    missing_supporting_outcomes = [
        outcome_id_value for outcome_id_value in supporting_outcomes
        if outcome_id_value not in outcomes_by_id
        or str(outcomes_by_id[outcome_id_value].get("study_id")) not in supporting
    ]
    missing_contradicting_outcomes = [
        outcome_id_value for outcome_id_value in contradicting_outcomes
        if outcome_id_value not in outcomes_by_id
        or str(outcomes_by_id[outcome_id_value].get("study_id")) not in contradicting
    ]
    has_study_linkage = bool(supporting or contradicting)
    has_explicit_linkage = bool(supporting_outcomes or contradicting_outcomes or supporting_refs or contradicting_refs)
    if has_study_linkage and not has_explicit_linkage:
        linkage_status = "study_only_linkage"
    elif (
        missing_supporting_outcomes or missing_contradicting_outcomes
        or unresolved_supporting_evidence or unresolved_contradicting_evidence
    ):
        linkage_status = "evidence_linkage_incomplete"
    elif has_study_linkage and (supporting_evidence or contradicting_evidence):
        linkage_status = "complete"
    else:
        linkage_status = "evidence_linkage_incomplete"
    explicit_evidence_locations = [
        {"study_id": study_id_value, "locations": [location]}
        for study_id_value in sorted({str(item.get("study_id")) for item in supporting_evidence})
        for location in supporting_evidence
        if str(location.get("study_id")) == study_id_value
    ]
    return {
        "claim_id": str(raw.get("claim_id") or stable_id("claim", section_id, sentence)),
        "section_id": section_id, "sentence": sentence,
        "supporting_studies": supporting, "contradicting_studies": contradicting,
        "supporting_outcomes": supporting_outcomes, "contradicting_outcomes": contradicting_outcomes,
        "supporting_evidence": supporting_evidence, "contradicting_evidence": contradicting_evidence,
        "unresolved_supporting_evidence": unresolved_supporting_evidence,
        "unresolved_contradicting_evidence": unresolved_contradicting_evidence,
        "missing_supporting_outcomes": missing_supporting_outcomes,
        "missing_contradicting_outcomes": missing_contradicting_outcomes,
        "linkage_status": linkage_status,
        "critical_claim": bool(supporting or contradicting),
        "support_level": support_level, "required_qualifiers": required,
        "missing_required_qualifiers": [value for value in required if str(value).casefold() not in sentence.casefold()],
        "evidence_locations": explicit_evidence_locations,
        "protocol_scope_status": scope,
        "population_evidence_levels": population_levels,
        "claimed_population_levels": claimed_population_levels,
        "animal_to_human": animal_to_human,
        "population_overgeneralization": population_overgeneralization,
        "causal_inflation": causal_inflation,
        "causal_strength": causal_strength,
        "unreported_field_claims": sorted(set(raw.get("unreported_field_claims", [])) | set(derived_unreported)),
        "omitted_contradicting_studies": omitted_contradictions,
        "invalid_supporting_studies": sorted(set(supporting) - valid_studies),
        "invalid_contradicting_studies": sorted(set(contradicting) - valid_studies),
        "audit_status": "pending",
    }


def _claim_field(study: dict[str, Any], path: str, default: Any = None) -> Any:
    fields = study.get("fields", {})
    if path in fields:
        return fields[path]
    if path in study:
        return study[path]
    return default


def _infer_claim_scope(project: ReviewProject, studies: list[dict[str, Any]]) -> str:
    statuses = {
        str(value) for study in studies
        if (value := _claim_field(study, "protocol.scope_status", _claim_field(study, "intervention.scope", "")))
    }
    if "outside" in statuses:
        return "outside"
    if "adjacent" in statuses:
        return "adjacent"
    if statuses.intersection({"inside", "core"}):
        return "inside"
    scope = project.protocol.data.get("scope", {})
    core = set(str(value) for value in scope.get("core_interventions", []))
    adjacent = set(str(value) for value in scope.get("adjacent_interventions", []))
    interventions = {
        str(value) for study in studies if (value := _claim_field(study, "intervention.summary", ""))
    }
    if interventions & adjacent:
        return "adjacent"
    if interventions and interventions.issubset(core):
        return "inside"
    return "unclear"
