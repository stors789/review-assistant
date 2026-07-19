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


def write_section(section_spec: dict[str, Any], evidence_bundle: list[dict[str, Any]], synthesis_context: dict[str, Any], mode: str, writer: Callable[..., str] | None = None) -> str:
    if mode not in {"explore", "review"}:
        raise ValueError("mode must be explore or review")
    if writer:
        return writer(section_spec=section_spec, evidence_bundle=evidence_bundle, synthesis_context=synthesis_context, mode=mode)
    if mode == "review" and not section_spec.get("included_study_ids"):
        return "Evidence is insufficient for this protocol-required section."
    ids = section_spec.get("included_study_ids", [])
    direction_labels = []
    for field, label in (("supporting_evidence", "supporting"), ("contradicting_evidence", "opposing"), ("neutral_evidence", "neutral"), ("mixed_evidence", "mixed")):
        values = section_spec.get(field, [])
        if values:
            direction_labels.append(f"{label}: {', '.join(values)}")
    citations = " ".join(f"[{sid}]" for sid in ids)
    return f"Evidence from {len(ids)} eligible studies was synthesized ({'; '.join(direction_labels) or 'direction unclear'}) {citations}."


def synthesize_review(project: ReviewProject, writer: Callable[..., str] | None = None) -> str:
    plan = resolve_synthesis_plan(project)
    memos = build_evidence_memos(project, plan)
    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for memo in memos:
        by_section[memo["section_id"]].append(memo)
    protocol = project.protocol.data
    title = protocol["review"].get("title") or "Review synthesis"
    sections = [f"# {title}", "", f"Protocol hash: `{plan['protocol_hash']}`", ""]
    draft_dir = project.root / "synthesis" / "section_drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    for spec in plan["sections"]:
        content = write_section(spec, by_section.get(spec["section_id"], []), {"protocol": protocol}, "review", writer)
        atomic_write_text(draft_dir / f"{spec['section_id']}.md", content + "\n")
        sections.extend([f"## {spec['title']}", "", content, ""])
    report = "\n".join(sections).rstrip() + "\n"
    atomic_write_text(project.root / "synthesis" / "review_draft.md", report)
    build_claim_map(project, report, plan)
    return report


def build_claim_map(project: ReviewProject, report: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan or resolve_synthesis_plan(project)
    valid_studies = set(eligible_study_ids(project))
    outcomes = read_jsonl(project.root / "extraction" / "outcomes.jsonl")
    locations = defaultdict(list)
    for outcome in outcomes:
        locations[outcome["study_id"]].extend(outcome.get("evidence", []))
    contradiction_payload = project.root / "evidence" / "contradiction_groups.json"
    contradiction_groups = json.loads(contradiction_payload.read_text())["groups"] if contradiction_payload.exists() else []
    claims = []
    current_section = ""
    title_to_id = {section["title"]: section["section_id"] for section in plan["sections"]}
    for line in report.splitlines():
        if line.startswith("## "):
            current_section = title_to_id.get(line[3:].strip(), "")
            continue
        for sentence in re.split(r"(?<=[.!?。！？])\s+", line.strip()):
            if not sentence or sentence.startswith("#") or sentence.startswith("Protocol hash:"):
                continue
            cited = sorted(set(re.findall(r"\[([^\[\]]*study_[0-9a-f]{16})\]", sentence)) & valid_studies)
            relevant_inconsistency = any(set(cited) & set(group.get("study_ids", [])) and group.get("has_directional_inconsistency") for group in contradiction_groups)
            contradicting = sorted({sid for group in contradiction_groups if set(cited) & set(group.get("study_ids", [])) and group.get("has_directional_inconsistency") for sid in group.get("study_ids", []) if sid not in cited})
            support = "unsupported" if not cited else ("mixed" if relevant_inconsistency else "supported")
            claims.append({
                "claim_id": stable_id("claim", current_section, sentence), "section_id": current_section,
                "sentence": sentence, "supporting_studies": cited, "contradicting_studies": contradicting,
                "support_level": support, "required_qualifiers": [],
                "evidence_locations": [{"study_id": sid, "locations": locations[sid]} for sid in cited],
                "protocol_scope_status": "inside", "audit_status": "pending",
            })
    result = {"schema_version": "1.0", "protocol_hash": project.track_protocol(), "claims": claims}
    write_json(project.root / "synthesis" / "claim_map.json", result)
    return result
