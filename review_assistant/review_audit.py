"""Deterministic, configuration-aware audit checks for formal reviews."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_text, load_yaml, read_jsonl, write_json
from .project import ReviewProject
from .review_synthesis import eligible_study_ids, resolve_synthesis_plan, validate_structured_section_output
from .eligibility import fulltext_requirement, latest_screening_decisions, resolve_eligibility, resolve_fulltext_status
from .screening import resolve_screening_completeness
from .studies import current_extraction_errors, evidence_location_id


AUDIT_OUTPUTS = {
    "unsupported_claims": "unsupported_claims.md",
    "scope_violations": "scope_violations.md",
    "citation_failures": "citation_audit.md",
    "invalid_quotes": "quote_audit.md",
    "contradiction_omissions": "contradiction_omissions.md",
    "protocol_compliance": "protocol_compliance.md",
}


def _issue(check: str, message: str, **context: Any) -> dict[str, Any]:
    return {"check": check, "message": message, "context": context}


def _extraction_evidence_index(project: ReviewProject) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Index current Outcome and Evidence records for independent audit resolution."""
    outcomes = {
        str(item.get("outcome_id")): item
        for item in read_jsonl(project.root / "extraction" / "outcomes.jsonl")
        if item.get("outcome_id")
    }
    evidence: dict[str, dict[str, Any]] = {}
    for outcome in outcomes.values():
        study_id_value = str(outcome.get("study_id", ""))
        outcome_id_value = str(outcome.get("outcome_id", ""))
        for ordinal, raw_location in enumerate(outcome.get("evidence", [])):
            location = dict(raw_location)
            location["evidence_id"] = evidence_location_id(study_id_value, outcome_id_value, location, ordinal)
            location["study_id"] = study_id_value
            location["outcome_id"] = outcome_id_value
            evidence[location["evidence_id"]] = location
    return outcomes, evidence


def _side_values(claim: dict[str, Any], side: str, suffix: str) -> list[str]:
    value = claim.get(f"{side}_{suffix}", [])
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _side_evidence_refs(claim: dict[str, Any], side: str) -> list[Any]:
    value = claim.get(f"{side}_evidence", [])
    return value if isinstance(value, list) else []


def _report_section_text(report: str, title: str) -> str:
    lines = report.splitlines()
    marker = f"## {title}"
    try:
        start = lines.index(marker) + 1
    except ValueError:
        return ""
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        collected.append(line)
    return "\n".join(collected).strip()


class ReviewAuditor:
    def __init__(self, project: ReviewProject):
        self.project = project

    def run(self) -> dict[str, Any]:
        protocol_hash = self.project.track_protocol()
        claim_path = self.project.root / "synthesis" / "claim_map.json"
        claim_map = json.loads(claim_path.read_text(encoding="utf-8")) if claim_path.exists() else {"claims": []}
        claims = claim_map.get("claims", [])
        valid_studies = set(eligible_study_ids(self.project))
        all_studies = {str(item.get("study_id")) for item in read_jsonl(self.project.root / "extraction" / "studies.jsonl") if item.get("study_id")}
        eligibility = resolve_eligibility(self.project)
        links = read_jsonl(self.project.root / "extraction" / "study_record_links.jsonl")
        linked_studies = {str(item.get("study_id")) for item in links if item.get("record_id") and item.get("study_id")}
        outcomes_by_id, evidence_by_id = _extraction_evidence_index(self.project)
        try:
            resolved_plan = resolve_synthesis_plan(self.project)
        except Exception:
            resolved_plan = {"sections": []}
        section_specs = {str(item.get("section_id")): item for item in resolved_plan.get("sections", [])}
        issues: list[dict[str, Any]] = []

        if claim_map.get("status") == "invalid":
            issues.append(_issue("claim_map_invalid", "Claim map was not generated because structured synthesis validation failed"))

        report_path = self.project.root / "synthesis" / "review_draft.md"
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        claims_by_section: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            section_id = str(claim.get("section_id", ""))
            claims_by_section.setdefault(section_id, []).append({
                "sentence": claim.get("sentence", ""),
                "supporting_study_ids": claim.get("supporting_studies", []),
                "contradicting_study_ids": claim.get("contradicting_studies", []),
            })
        for section_id, spec in section_specs.items():
            section_text = _report_section_text(report, str(spec.get("title", "")))
            if not section_text and spec.get("included_study_ids"):
                issues.append(_issue("section_text_missing", "Evidence section text is missing from the draft", section_id=section_id))
                continue
            independent = validate_structured_section_output(
                spec, {"section_text": section_text, "claims": claims_by_section.get(section_id, [])}, valid_studies,
            )
            if independent.get("coverage_status") != "passed":
                issues.append(_issue("claim_coverage_incomplete", "Independent section/claim coverage validation failed", section_id=section_id, errors=independent.get("errors", []), unmapped_citations=independent.get("unmapped_citations", []), unmapped_sentences=independent.get("unmapped_sentences", [])))

        for claim in claims:
            cid = claim.get("claim_id")
            supporting = _side_values(claim, "supporting", "studies")
            contradicting = _side_values(claim, "contradicting", "studies")
            if claim.get("support_level") == "unsupported" or not supporting:
                issues.append(_issue("unsupported_claim", "Claim lacks supporting study evidence", claim_id=cid))
                issues.append(_issue("missing_citation", "Claim has no resolved study citation", claim_id=cid))
            scope = claim.get("protocol_scope_status")
            if scope == "outside":
                issues.append(_issue("scope_violation", "Claim is outside protocol scope", claim_id=cid))
            if scope == "adjacent":
                issues.append(_issue("adjacent_intervention_leakage", "Adjacent evidence is presented as in-scope", claim_id=cid))
            for flag, check, message in (
                ("population_overgeneralization", "population_level_overgeneralization", "Claim generalizes beyond its population evidence"),
                ("animal_to_human", "animal_to_human_extrapolation", "Claim extrapolates across configured population levels"),
                ("causal_inflation", "correlation_to_causation_inflation", "Claim uses causal language beyond its evidence design"),
            ):
                if claim.get(flag):
                    issues.append(_issue(check, message, claim_id=cid))

            spec = section_specs.get(str(claim.get("section_id")), {})
            section_studies = {str(value) for value in spec.get("included_study_ids", [])}
            relation_explanation = claim.get("support_relation_explanation") or claim.get("relation_explanation")
            for side in ("supporting", "contradicting"):
                side_studies = _side_values(claim, side, "studies")
                side_outcomes = _side_values(claim, side, "outcomes")
                side_evidence = _side_evidence_refs(claim, side)
                unresolved_evidence = claim.get(f"unresolved_{side}_evidence", [])
                if not isinstance(unresolved_evidence, list):
                    unresolved_evidence = []
                counts = Counter(side_studies)
                duplicated = sorted(sid for sid, count in counts.items() if count > 1)
                if duplicated:
                    issues.append(_issue("duplicate_study_counting", "A study is counted more than once in one claim", claim_id=cid, side=side, study_ids=duplicated))
                unknown = sorted(set(side_studies) - all_studies)
                if unknown:
                    issues.append(_issue(f"invalid_{side}_study", "Claim cites an unknown study", claim_id=cid, study_ids=unknown))
                    issues.append(_issue("citation_key_resolution_failure", "Study citation cannot be resolved", claim_id=cid, study_ids=unknown))
                excluded = sorted((set(side_studies) & all_studies) - valid_studies)
                if excluded:
                    issues.append(_issue(f"invalid_{side}_study", "Claim cites a study outside the eligible evidence set", claim_id=cid, study_ids=excluded))
                    issues.append(_issue("citation_to_excluded_study", "Claim cites a study excluded by the shared eligibility gate", claim_id=cid, study_ids=excluded))
                outside = sorted(set(side_studies) - section_studies)
                if outside:
                    issues.append(_issue(f"{side}_study_outside_section", "Claim cites a study outside this section's evidence set", claim_id=cid, study_ids=outside))
                unlinked = sorted(set(side_studies) & (all_studies - linked_studies))
                if unlinked and eligibility.enforcement == "required":
                    issues.append(_issue("citation_to_unlinked_study", "Claim cites a study without a source-record link", claim_id=cid, side=side, study_ids=unlinked))

                if side_studies and not side_outcomes:
                    issues.append(_issue(f"{side}_outcome_unresolved", "Every cited study must be linked to a specific outcome", claim_id=cid, study_ids=side_studies))
                for outcome_id_value in side_outcomes:
                    outcome = outcomes_by_id.get(outcome_id_value)
                    if outcome is None:
                        issues.append(_issue(f"{side}_outcome_unresolved", "Claim outcome cannot be resolved", claim_id=cid, outcome_ids=[outcome_id_value]))
                        continue
                    outcome_study = str(outcome.get("study_id", ""))
                    if outcome_study not in side_studies:
                        issues.append(_issue(f"{side}_outcome_wrong_study", "Claim outcome does not belong to a study cited on the same side", claim_id=cid, outcome_id=outcome_id_value, study_id=outcome_study))

                if side_studies and not side_evidence and not unresolved_evidence:
                    issues.append(_issue(f"{side}_evidence_unresolved", "Every critical citation must be linked to a specific evidence location", claim_id=cid, study_ids=side_studies))
                resolved_side_evidence = False
                for reference in side_evidence:
                    evidence_id_value = str(reference.get("evidence_id", "")) if isinstance(reference, dict) else str(reference)
                    evidence = evidence_by_id.get(evidence_id_value)
                    if evidence is None:
                        issues.append(_issue(f"{side}_evidence_unresolved", "Claim evidence location cannot be resolved", claim_id=cid, evidence_ids=[evidence_id_value]))
                        continue
                    resolved_side_evidence = True
                    evidence_study = str(evidence.get("study_id", ""))
                    evidence_outcome = str(evidence.get("outcome_id", ""))
                    if evidence_study not in side_studies:
                        issues.append(_issue(f"{side}_evidence_wrong_study", "Claim evidence belongs to a different study", claim_id=cid, evidence_id=evidence_id_value, study_id=evidence_study))
                    if evidence_outcome not in side_outcomes:
                        issues.append(_issue(f"{side}_evidence_wrong_outcome", "Claim evidence belongs to a different outcome", claim_id=cid, evidence_id=evidence_id_value, outcome_id=evidence_outcome))
                    status = str(evidence.get("validation_status", "unverified"))
                    if status == "failed":
                        issues.append(_issue("invalid_quote", "A quote supporting this claim failed source verification", claim_id=cid, evidence_id=evidence_id_value))
                    elif status != "passed":
                        issues.append(_issue("unverified_critical_quote", "Claim depends on evidence that is not passed or manual-verified", claim_id=cid, evidence_id=evidence_id_value, validation_status=status))
                    outcome = outcomes_by_id.get(evidence_outcome)
                    relation = str(outcome.get("support_relation", "unclear")) if outcome else "unclear"
                    if side == "supporting" and relation == "contradicts":
                        issues.append(_issue("supporting_support_relation_mismatch", "Supporting evidence is structurally marked as contradicting", claim_id=cid, outcome_id=evidence_outcome))
                        issues.append(_issue("support_relation_mismatch", "Claim side disagrees with outcome support relation", claim_id=cid, outcome_id=evidence_outcome, side=side))
                    elif side == "contradicting" and relation == "supports":
                        issues.append(_issue("contradicting_support_relation_mismatch", "Contradicting evidence is structurally marked as supporting", claim_id=cid, outcome_id=evidence_outcome))
                        issues.append(_issue("support_relation_mismatch", "Claim side disagrees with outcome support relation", claim_id=cid, outcome_id=evidence_outcome, side=side))
                    elif relation in {"unclear", "mixed", "neutral"} and not relation_explanation:
                        issues.append(_issue(f"{side}_support_relation_unclear", "Claim must explain that an unclear, mixed, or neutral relation is not definite evidence", claim_id=cid, outcome_id=evidence_outcome, support_relation=relation))
                if side_studies and side_outcomes and not resolved_side_evidence and not unresolved_evidence:
                    issues.append(_issue(f"{side}_evidence_unresolved", "Specific outcome has no resolvable evidence location", claim_id=cid, outcome_ids=side_outcomes))

            if claim.get("unreported_field_claims"):
                issues.append(_issue("unreported_field_hallucination", "Claim asserts fields recorded as unreported", claim_id=cid, fields=claim["unreported_field_claims"]))
            if claim.get("missing_required_qualifiers"):
                issues.append(_issue("missing_required_qualifier", "Claim omits a configured required qualifier", claim_id=cid, qualifiers=claim["missing_required_qualifiers"]))
            if claim.get("omitted_contradicting_studies"):
                issues.append(_issue("contradiction_omission", "Claim omits known contradictory studies", claim_id=cid, study_ids=claim["omitted_contradicting_studies"]))
            if claim.get("critical_claim") and supporting and not _side_evidence_refs(claim, "supporting"):
                issues.append(_issue("supporting_evidence_unresolved", "Critical supporting claim lacks a concrete evidence location", claim_id=cid))

        extraction_errors = current_extraction_errors(self.project)
        for error in extraction_errors:
            if error.get("error") == "quote_verification_failed":
                issues.append(_issue("invalid_quote", "Extracted quote failed source verification", study_id=error.get("study_id")))
            if error.get("error") in {"schema_validation_failed", "outcome_schema_validation_failed"}:
                issues.append(_issue("schema_validation_error", "Structured extraction violates the configured schema", study_id=error.get("study_id"), field=error.get("field")))

        contradiction_path = self.project.root / "evidence" / "contradiction_groups.json"
        groups = json.loads(contradiction_path.read_text(encoding="utf-8")).get("groups", []) if contradiction_path.exists() else []
        for group in groups:
            if not group.get("has_directional_inconsistency"):
                continue
            group_ids = set(group.get("study_ids", []))
            for claim in claims:
                accounted = set(claim.get("supporting_studies", [])) | set(claim.get("contradicting_studies", []))
                if group_ids & accounted and not group_ids.issubset(accounted):
                    issues.append(_issue("contradiction_omission", "Claim omits known directionally inconsistent evidence", claim_id=claim.get("claim_id"), group_id=group.get("group_id")))

        report_path = self.project.root / "synthesis" / "review_draft.md"
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        headings = {line[3:].strip() for line in report.splitlines() if line.startswith("## ")}
        required = self.project.protocol.data.get("synthesis", {}).get("required_sections", [])
        required_titles = {item if isinstance(item, str) else item.get("title", "") for item in required}
        for title in sorted(required_titles - headings):
            issues.append(_issue("missing_required_section", "Protocol-required section is missing", title=title))
        if claim_map.get("protocol_hash") and claim_map.get("protocol_hash") != protocol_hash:
            issues.append(_issue("protocol_hash_mismatch", "Claim map was produced under a different protocol", expected=protocol_hash, actual=claim_map.get("protocol_hash")))
        for link in links:
            if link.get("protocol_hash") and link.get("protocol_hash") != protocol_hash:
                issues.append(_issue("protocol_hash_mismatch", "Study link was produced under a different protocol", study_id=link.get("study_id"), actual=link.get("protocol_hash"), expected=protocol_hash))

        section_validation_path = self.project.root / "synthesis" / "section_validation.json"
        section_validation = json.loads(section_validation_path.read_text(encoding="utf-8")) if section_validation_path.exists() else {}
        if not section_validation_path.exists():
            issues.append(_issue("section_coverage_missing", "Structured section coverage validation artifact is missing"))
        elif section_validation.get("protocol_hash") and section_validation.get("protocol_hash") != protocol_hash:
            issues.append(_issue("protocol_hash_mismatch", "Section validation was produced under a different protocol", actual=section_validation.get("protocol_hash"), expected=protocol_hash))
        if section_validation.get("status") == "failed":
            issues.append(_issue("section_coverage_incomplete", "Section text and claim map coverage validation failed"))
        validated_sections = {str(item.get("section_id")): item for item in section_validation.get("sections", []) if isinstance(item, dict)}
        for section_id, spec in section_specs.items():
            item = validated_sections.get(section_id)
            if item is None:
                issues.append(_issue("section_coverage_missing", "Protocol-required section lacks coverage metadata", section_id=section_id))
                continue
            if item.get("evidence_status") == "insufficient" or not spec.get("included_study_ids"):
                if item.get("writer_called"):
                    issues.append(_issue("empty_evidence_writer_called", "A section without evidence invoked a writer", section_id=section_id))
                if item.get("claim_count", 0):
                    issues.append(_issue("empty_evidence_section_has_claims", "A section without evidence contains claims", section_id=section_id))
            elif item.get("claim_count", 0) == 0:
                issues.append(_issue("nonempty_section_zero_claims", "A non-empty evidence section contains no claims", section_id=section_id))
            if item.get("unmapped_citations") or item.get("unmapped_sentences") or item.get("coverage_status") != "passed":
                issues.append(_issue("claim_coverage_incomplete", "Section citations or substantive sentences are not fully mapped", section_id=section_id, unmapped_citations=item.get("unmapped_citations", []), unmapped_sentences=item.get("unmapped_sentences", [])))

        memo_ids: set[str] = set()
        memo_dir = self.project.root / "evidence" / "evidence_memos"
        if memo_dir.exists():
            for summary_path in memo_dir.glob("*_summary.json"):
                memo_ids.update(json.loads(summary_path.read_text()).get("all_study_ids", []))
        dropped = sorted(valid_studies - memo_ids)
        if dropped:
            issues.append(_issue("dropped_eligible_evidence", "Eligible studies are absent from evidence memos", study_ids=dropped))

        cited_studies = {sid for claim in claims for sid in claim.get("supporting_studies", []) + claim.get("contradicting_studies", [])}
        included_ineligible = sorted((cited_studies & all_studies) - valid_studies)
        if included_ineligible:
            issues.append(_issue("included_ineligible_evidence", "Draft or claim map includes ineligible studies", study_ids=included_ineligible))

        metadata_path = self.project.root / "synthesis" / "synthesis_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        if metadata.get("placeholder"):
            issues.append(_issue("placeholder_synthesis_used", "Placeholder synthesis cannot pass a formal Review audit"))

        search_plan = load_yaml(self.project.root / "search_plan.yaml")
        enabled_searches = [item for item in search_plan.get("searches", []) if item.get("enabled", True)]
        if not enabled_searches and not search_plan.get("seed_records"):
            issues.append(_issue("empty_search_plan", "No enabled searches or seed records are configured"))

        if eligibility.enforcement == "required":
            completeness = resolve_screening_completeness(self.project)
            incomplete_ids = sorted(set(completeness["title_abstract_missing_ids"]) | set(completeness["title_abstract_uncertain_ids"]) | set(completeness["fulltext_missing_ids"]) | set(completeness["fulltext_uncertain_ids"]) | set(completeness["illegal_state_ids"]))
            if incomplete_ids:
                issues.append(_issue("screening_required_but_incomplete", "Required screening has missing, uncertain, or illegal final decisions", record_ids=incomplete_ids, title_abstract_missing_ids=completeness["title_abstract_missing_ids"], title_abstract_uncertain_ids=completeness["title_abstract_uncertain_ids"], fulltext_missing_ids=completeness["fulltext_missing_ids"], fulltext_uncertain_ids=completeness["fulltext_uncertain_ids"], illegal_state_ids=completeness["illegal_state_ids"]))
            if eligibility.unlinked_included_record_ids:
                issues.append(_issue("included_record_lacking_study_link", "A full-text included record has no study link", record_ids=sorted(eligibility.unlinked_included_record_ids)))
            if eligibility.unlinked_study_ids:
                issues.append(_issue("study_lacking_source_record", "An extracted study has no source record while screening is required", study_ids=sorted(eligibility.unlinked_study_ids)))

        fulltext = resolve_fulltext_status(self.project)
        if fulltext_requirement(self.project) == "required" and fulltext["fulltext_missing_record_ids"]:
            issues.append(_issue("included_record_lacking_full_text", "An included record has no available full text", record_ids=fulltext["fulltext_missing_record_ids"]))
        elif fulltext_requirement(self.project) == "structured_extraction_allowed" and fulltext["study_link_missing_record_ids"]:
            issues.append(_issue("included_record_lacking_structured_extraction", "An included record has no structured extraction", record_ids=fulltext["study_link_missing_record_ids"]))

        categories: dict[str, list[dict[str, Any]]] = {key: [] for key in AUDIT_OUTPUTS}
        for item in issues:
            check = item["check"]
            if check in {"unsupported_claim", "missing_citation"}:
                categories["unsupported_claims"].append(item)
            elif check in {"scope_violation", "adjacent_intervention_leakage", "population_level_overgeneralization", "animal_to_human_extrapolation", "correlation_to_causation_inflation", "unreported_field_hallucination"}:
                categories["scope_violations"].append(item)
            elif check in {"invalid_quote", "unverified_critical_quote"}:
                categories["invalid_quotes"].append(item)
            elif check == "contradiction_omission":
                categories["contradiction_omissions"].append(item)
            elif check in {"citation_key_resolution_failure", "duplicate_study_counting", "citation_to_excluded_study", "citation_to_unlinked_study", "included_ineligible_evidence"}:
                categories["citation_failures"].append(item)
            else:
                categories["protocol_compliance"].append(item)
        output = self.project.root / "audit"
        for category, filename in AUDIT_OUTPUTS.items():
            lines = [f"# {category.replace('_', ' ').title()}", ""]
            lines.extend(f"- [{item['check']}] {item['message']} — `{json.dumps(item['context'], ensure_ascii=False, sort_keys=True)}`" for item in categories[category])
            if not categories[category]:
                lines.append("No issues detected.")
            atomic_write_text(output / filename, "\n".join(lines) + "\n")
        summary = {
            "schema_version": "1.0", "protocol_hash": protocol_hash,
            "status": "failed" if issues else "passed", "issue_count": len(issues),
            "counts": dict(sorted(Counter(item["check"] for item in issues).items())), "issues": issues,
        }
        write_json(output / "audit_summary.json", summary)
        return summary
