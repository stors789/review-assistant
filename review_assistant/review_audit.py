"""Deterministic, configuration-aware audit checks for formal reviews."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_text, load_yaml, read_jsonl, write_json
from .project import ReviewProject
from .review_synthesis import eligible_study_ids
from .eligibility import latest_screening_decisions, resolve_eligibility


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
        issues: list[dict[str, Any]] = []

        for claim in claims:
            cid = claim.get("claim_id")
            supporting = claim.get("supporting_studies", [])
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
            counts = Counter(supporting)
            duplicated = sorted(sid for sid, count in counts.items() if count > 1)
            if duplicated:
                issues.append(_issue("duplicate_study_counting", "A study is counted more than once in one claim", claim_id=cid, study_ids=duplicated))
            unresolved = sorted(set(supporting) - valid_studies)
            if unresolved:
                issues.append(_issue("citation_key_resolution_failure", "Study citation cannot be resolved", claim_id=cid, study_ids=unresolved))
            excluded = sorted((set(supporting) & all_studies) - valid_studies)
            if excluded:
                issues.append(_issue("citation_to_excluded_study", "Claim cites a study excluded by the shared eligibility gate", claim_id=cid, study_ids=excluded))
            unlinked = sorted(set(supporting) & (all_studies - linked_studies))
            if unlinked and eligibility.enforcement == "required":
                issues.append(_issue("citation_to_unlinked_study", "Claim cites a study without a source-record link", claim_id=cid, study_ids=unlinked))
            if claim.get("unreported_field_claims"):
                issues.append(_issue("unreported_field_hallucination", "Claim asserts fields recorded as unreported", claim_id=cid, fields=claim["unreported_field_claims"]))
            if claim.get("missing_required_qualifiers"):
                issues.append(_issue("missing_required_qualifier", "Claim omits a configured required qualifier", claim_id=cid, qualifiers=claim["missing_required_qualifiers"]))
            if claim.get("omitted_contradicting_studies"):
                issues.append(_issue("contradiction_omission", "Claim omits known contradictory studies", claim_id=cid, study_ids=claim["omitted_contradicting_studies"]))
            claim_locations = [
                location for group in claim.get("evidence_locations", [])
                for location in group.get("locations", [])
            ]
            if any(location.get("validation_status") == "failed" for location in claim_locations):
                issues.append(_issue("invalid_quote", "A quote supporting this claim failed source verification", claim_id=cid))
            if supporting and (not claim_locations or all(location.get("validation_status") == "unverified" for location in claim_locations)):
                issues.append(_issue("unverified_critical_quote", "Claim depends only on unverified quote evidence", claim_id=cid))

        extraction_errors = read_jsonl(self.project.root / "extraction" / "extraction_errors.jsonl")
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
            if eligibility.incomplete_record_ids or eligibility.illegal_states:
                issues.append(_issue("screening_required_but_incomplete", "Required screening has missing, uncertain, or illegal final decisions", record_ids=sorted(eligibility.incomplete_record_ids), illegal_states=eligibility.illegal_states))
            if eligibility.unlinked_included_record_ids:
                issues.append(_issue("included_record_lacking_study_link", "A full-text included record has no study link", record_ids=sorted(eligibility.unlinked_included_record_ids)))
            if eligibility.unlinked_study_ids:
                issues.append(_issue("study_lacking_source_record", "An extracted study has no source record while screening is required", study_ids=sorted(eligibility.unlinked_study_ids)))
            latest = latest_screening_decisions(self.project)
            title_includes = {rid for (rid, stage), item in latest.items() if stage == "title_abstract" and item.get("decision") == "include"}
            missing_fulltext_decision = sorted(rid for rid in title_includes if (rid, "fulltext") not in latest)
            if missing_fulltext_decision:
                issues.append(_issue("included_record_lacking_full_text", "Title/abstract included record lacks a full-text decision", record_ids=missing_fulltext_decision))

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
