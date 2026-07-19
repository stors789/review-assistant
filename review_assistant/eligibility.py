"""Single source of truth for Review screening eligibility and record links."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .io_utils import read_jsonl, write_json
from .project import ReviewProject
from .screening import latest_screening_decisions as _latest_screening_decisions
from .screening import resolve_screening_completeness
from .studies import current_studies

ENFORCEMENT_LEVELS = {"required", "optional", "disabled"}
FULLTEXT_REQUIREMENTS = {"required", "structured_extraction_allowed", "disabled"}


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
    screening_completeness: dict[str, Any] = field(default_factory=dict)


def screening_enforcement(project: ReviewProject) -> str:
    value = str(project.protocol.data.get("screening", {}).get("enforcement", "optional"))
    if value not in ENFORCEMENT_LEVELS:
        raise ValueError(f"screening.enforcement must be one of {sorted(ENFORCEMENT_LEVELS)}")
    return value


def fulltext_requirement(project: ReviewProject) -> str:
    value = str(project.protocol.data.get("fulltext", {}).get("requirement", "required"))
    if value not in FULLTEXT_REQUIREMENTS:
        raise ValueError(f"fulltext.requirement must be one of {sorted(FULLTEXT_REQUIREMENTS)}")
    return value


def _resource_record_ids(project: ReviewProject) -> set[str]:
    """Read explicit resource manifests without treating arbitrary URLs as full text."""
    result: set[str] = set()
    candidates = [
        project.root / "fulltext" / "resources.jsonl",
        project.root / "extraction" / "fulltext_resources.jsonl",
    ]
    for path in candidates:
        for item in read_jsonl(path):
            record_id = str(item.get("record_id", ""))
            if not record_id or item.get("available") is False:
                continue
            if any(item.get(key) for key in ("path", "file", "uri", "resource_id", "content", "text")) or item.get("available") is True:
                result.add(record_id)
    manifest = project.root / "fulltext" / "resources.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = []
        values = payload.get("resources", []) if isinstance(payload, dict) else payload
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict) or item.get("available") is False:
                    continue
                record_id = str(item.get("record_id", ""))
                if record_id and (any(item.get(key) for key in ("path", "file", "uri", "resource_id", "content", "text")) or item.get("available") is True):
                    result.add(record_id)
    return result


def _existing_file(project: ReviewProject, value: Any) -> bool:
    if not value:
        return False
    candidate = Path(str(value))
    choices = [candidate] if candidate.is_absolute() else [project.root / candidate, project.root / "fulltext" / candidate]
    return any(path.is_file() for path in choices)


def resolve_fulltext_status(project: ReviewProject) -> dict[str, Any]:
    """Separate local/declared full text from structured extraction availability."""
    requirement = fulltext_requirement(project)
    eligible = resolve_eligibility(project)
    included = set(eligible.included_record_ids)
    records = {str(item.get("record_id")): item for item in read_jsonl(project.root / "search" / "deduplicated_records.jsonl") if item.get("record_id")}
    fulltext_available: set[str] = set(_resource_record_ids(project)) & included

    for record_id in included:
        record = records.get(record_id, {})
        path_values = [record.get(key) for key in ("source_file", "full_text_file", "fulltext_file", "fulltext_path", "full_text_path", "pdf_path", "file")]
        configured_resource = record.get("fulltext_resource")
        if isinstance(configured_resource, dict):
            path_values.extend(configured_resource.get(key) for key in ("path", "file"))
        if any(_existing_file(project, value) for value in path_values):
            fulltext_available.add(record_id)
            continue
        names = {Path(str(value)).name for value in path_values if value}
        names.update({record_id, f"{record_id}.pdf", f"{record_id}.txt", f"{record_id}.md"})
        fulltext_dir = project.root / "fulltext"
        if fulltext_dir.exists() and any(path.is_file() and path.name in names for path in fulltext_dir.iterdir()):
            fulltext_available.add(record_id)

    latest_links: dict[str, dict[str, Any]] = {}
    for link in read_jsonl(project.root / "extraction" / "study_record_links.jsonl"):
        study_id_value = str(link.get("study_id", ""))
        if study_id_value:
            latest_links[study_id_value] = link
    study_ids = {str(item.get("study_id")) for item in current_studies(project) if item.get("study_id")}
    structured_available = {
        str(link.get("record_id")) for study_id_value, link in latest_links.items()
        if study_id_value in study_ids and link.get("record_id") and str(link.get("record_id")) in included
    }
    both = fulltext_available & structured_available
    result = {
        "schema_version": "1.0",
        "requirement": requirement,
        "included_record_ids": sorted(included),
        "fulltext_available_record_ids": sorted(fulltext_available),
        "structured_extraction_available_record_ids": sorted(structured_available),
        "both_available_record_ids": sorted(both),
        "fulltext_missing_record_ids": sorted(included - fulltext_available),
        "study_link_missing_record_ids": sorted(included - structured_available),
        # Compatibility aliases used by the existing CLI/tutorials.
        "available_record_ids": sorted(fulltext_available),
        "missing_record_ids": sorted(included - fulltext_available),
    }
    result["blocking_record_ids"] = sorted(
        (included - fulltext_available) if requirement == "required"
        else (included - structured_available) if requirement == "structured_extraction_allowed"
        else set()
    )
    result["screening_enforcement"] = eligible.enforcement
    write_json(project.root / "fulltext" / "status.json", result)
    return result


def latest_screening_decisions(project: ReviewProject) -> dict[tuple[str, str], dict[str, Any]]:
    return _latest_screening_decisions(project)


def resolve_eligibility(project: ReviewProject) -> EligibilityResult:
    """Resolve records, publications, and studies through one screening gate."""
    enforcement = screening_enforcement(project)
    completeness = resolve_screening_completeness(project)
    studies = {
        str(item["study_id"]): item
        for item in current_studies(project)
        if item.get("study_id")
    }
    latest_links: dict[str, dict[str, Any]] = {}
    for link in read_jsonl(project.root / "extraction" / "study_record_links.jsonl"):
        if link.get("study_id") and link.get("status", "active") == "active":
            latest_links[str(link["study_id"])] = link
    decisions = latest_screening_decisions(project)
    fulltext = {rid: item for (rid, stage), item in decisions.items() if stage == "fulltext"}
    screened_included = {rid for rid, item in fulltext.items() if item.get("decision") == "include"}
    screened_included &= set(completeness["all_search_record_ids"])
    if enforcement == "required":
        included = screened_included & set(completeness["title_abstract_included_ids"])
    else:
        included = screened_included
    result = EligibilityResult(enforcement=enforcement, included_record_ids=included, screening_completeness=completeness)

    for rid, item in fulltext.items():
        if item.get("decision") not in {"include", "exclude", "uncertain", "duplicate"}:
            result.illegal_states.append({"record_id": rid, "stage": "fulltext", "decision": item.get("decision")})
    if enforcement == "required":
        result.incomplete_record_ids = set(completeness["title_abstract_missing_ids"])
        result.incomplete_record_ids.update(completeness["title_abstract_uncertain_ids"])
        result.incomplete_record_ids.update(completeness["fulltext_missing_ids"])
        result.incomplete_record_ids.update(completeness["fulltext_uncertain_ids"])
        result.incomplete_record_ids.update(completeness["illegal_state_ids"])
        result.illegal_states = [
            {"record_id": record_id, "stage": "screening", "decision": "illegal"}
            for record_id in completeness["illegal_state_ids"]
        ]

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
