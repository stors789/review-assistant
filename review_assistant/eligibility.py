"""Single source of truth for Review screening eligibility and record links."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .io_utils import read_jsonl
from .project import ReviewProject

ENFORCEMENT_LEVELS = {"required", "optional", "disabled"}


@dataclass
class EligibilityResult:
    enforcement: str
    eligible_record_ids: set[str] = field(default_factory=set)
    eligible_publication_ids: set[str] = field(default_factory=set)
    eligible_study_ids: set[str] = field(default_factory=set)
    included_record_ids: set[str] = field(default_factory=set)
    incomplete_record_ids: set[str] = field(default_factory=set)
    unlinked_included_record_ids: set[str] = field(default_factory=set)
    unlinked_study_ids: set[str] = field(default_factory=set)
    illegal_states: list[dict[str, Any]] = field(default_factory=list)


def screening_enforcement(project: ReviewProject) -> str:
    value = str(project.protocol.data.get("screening", {}).get("enforcement", "optional"))
    if value not in ENFORCEMENT_LEVELS:
        raise ValueError(f"screening.enforcement must be one of {sorted(ENFORCEMENT_LEVELS)}")
    return value


def latest_screening_decisions(project: ReviewProject) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in read_jsonl(project.root / "screening" / "decision_history.jsonl"):
        record_id = str(item.get("record_id", ""))
        stage = str(item.get("stage", ""))
        if record_id and stage in {"title_abstract", "fulltext"}:
            latest[(record_id, stage)] = item
    return latest


def resolve_eligibility(project: ReviewProject) -> EligibilityResult:
    """Resolve records, publications, and studies through one screening gate."""
    enforcement = screening_enforcement(project)
    studies = {
        str(item["study_id"]): item
        for item in read_jsonl(project.root / "extraction" / "studies.jsonl")
        if item.get("study_id")
    }
    latest_links: dict[str, dict[str, Any]] = {}
    for link in read_jsonl(project.root / "extraction" / "study_record_links.jsonl"):
        if link.get("study_id"):
            latest_links[str(link["study_id"])] = link
    decisions = latest_screening_decisions(project)
    fulltext = {rid: item for (rid, stage), item in decisions.items() if stage == "fulltext"}
    title_abstract = {rid: item for (rid, stage), item in decisions.items() if stage == "title_abstract"}
    included = {rid for rid, item in fulltext.items() if item.get("decision") == "include"}
    result = EligibilityResult(enforcement=enforcement, included_record_ids=included)

    for rid, item in fulltext.items():
        if item.get("decision") not in {"include", "exclude", "uncertain", "duplicate"}:
            result.illegal_states.append({"record_id": rid, "stage": "fulltext", "decision": item.get("decision")})
    if enforcement == "required":
        candidate_records = set(title_abstract) | set(fulltext)
        result.incomplete_record_ids = {
            rid for rid in candidate_records
            if rid not in fulltext or fulltext[rid].get("decision") not in {"include", "exclude", "duplicate"}
        }

    if enforcement == "disabled" or (enforcement == "optional" and not fulltext):
        result.eligible_study_ids = set(studies)
        result.eligible_publication_ids = {str(item.get("publication_id", "")) for item in studies.values() if item.get("publication_id")}
        result.unlinked_study_ids = set(studies) - set(latest_links)
        return result

    result.eligible_record_ids = set(included)
    for sid, study in studies.items():
        link = latest_links.get(sid)
        if not link or not link.get("record_id"):
            result.unlinked_study_ids.add(sid)
            continue
        if str(link["record_id"]) in included:
            result.eligible_study_ids.add(sid)
            result.eligible_publication_ids.add(str(study.get("publication_id", "")))
    linked_records = {str(link.get("record_id")) for link in latest_links.values() if link.get("record_id")}
    result.unlinked_included_record_ids = included - linked_records
    return result


def resolve_eligible_records(project: ReviewProject) -> list[str]:
    return sorted(resolve_eligibility(project).eligible_record_ids)


def resolve_eligible_publications(project: ReviewProject) -> list[str]:
    return sorted(resolve_eligibility(project).eligible_publication_ids)


def resolve_eligible_studies(project: ReviewProject) -> list[str]:
    return sorted(resolve_eligibility(project).eligible_study_ids)
