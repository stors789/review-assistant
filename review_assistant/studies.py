"""Publication/study hierarchy and schema-driven structured extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .io_utils import append_jsonl, stable_id
from .project import ReviewProject


@dataclass
class EvidenceLocation:
    quote: str
    page: str = ""
    section: str = ""
    figure: str = ""
    table: str = ""
    validation_status: str = "pending"


@dataclass
class Outcome:
    outcome_id: str
    study_id: str
    domain: str
    direction: str
    value: Any = None
    evidence: list[EvidenceLocation] = field(default_factory=list)


@dataclass
class Arm:
    arm_id: str
    study_id: str
    role: str
    label: str
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceLocation] = field(default_factory=list)


@dataclass
class Study:
    study_id: str
    publication_id: str
    label: str
    fields: dict[str, Any]
    arms: list[Arm] = field(default_factory=list)
    outcome_ids: list[str] = field(default_factory=list)
    evidence: dict[str, list[EvidenceLocation]] = field(default_factory=dict)
    manually_revised: bool = False


@dataclass
class Publication:
    publication_id: str
    title: str
    doi: str = ""
    year: str = ""
    authors: list[str] = field(default_factory=list)
    study_ids: list[str] = field(default_factory=list)


def publication_id(record: dict[str, Any]) -> str:
    identity = record.get("doi") or record.get("pmid") or record.get("title")
    return stable_id("pub", identity)


def study_id(publication: str, label: str, ordinal: int) -> str:
    return stable_id("study", publication, label, ordinal)


def field_focus_terms(schema_data: dict[str, Any]) -> list[str]:
    """Build domain-neutral EvidencePack focus terms from external field metadata."""
    terms: list[str] = []
    for name, spec in schema_data.get("fields", {}).items():
        terms.extend(str(name).replace(".", " ").split())
        for key in ("description", "extraction_instruction"):
            if spec.get(key):
                terms.append(str(spec[key]))
        aliases = spec.get("aliases", [])
        if isinstance(aliases, list):
            terms.extend(str(alias) for alias in aliases)
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))


def build_schema_evidence_pack(text: str, schema_data: dict[str, Any], **kwargs: Any) -> tuple[str, dict[str, Any]]:
    from .evidence_pack import build_evidence_pack
    focus = " ; ".join(field_focus_terms(schema_data))
    return build_evidence_pack(text, focus, **kwargs)


class StudyExtractionStore:
    """Validate and persist model- or human-produced extraction records."""

    def __init__(self, project: ReviewProject, quote_validator: Callable[[str], bool] | None = None):
        self.project = project
        self.schema = project.extraction_schema
        self.quote_validator = quote_validator or (lambda quote: bool(quote.strip()))

    def ingest(self, record: dict[str, Any]) -> tuple[Publication, list[Study], list[Outcome]]:
        pub_data = record.get("publication")
        study_data = record.get("studies")
        if not isinstance(pub_data, dict) or not isinstance(study_data, list):
            raise ValueError("extraction requires publication mapping and studies list")
        pub_id = publication_id(pub_data)
        publication = Publication(
            publication_id=pub_id, title=str(pub_data.get("title", "")), doi=str(pub_data.get("doi", "")),
            year=str(pub_data.get("year", "")), authors=[str(author) for author in pub_data.get("authors", [])],
        )
        studies: list[Study] = []
        outcomes: list[Outcome] = []
        errors: list[dict[str, Any]] = []
        for index, raw in enumerate(study_data):
            label = str(raw.get("label") or f"study-{index + 1}")
            sid = str(raw.get("study_id") or study_id(pub_id, label, index))
            fields = self.schema.apply_missing_values(dict(raw.get("fields", {})))
            evidence = self._evidence_map(raw.get("evidence", {}), sid, errors)
            arms = []
            for arm_index, arm in enumerate(raw.get("arms", [])):
                aid = str(arm.get("arm_id") or stable_id("arm", sid, arm.get("role"), arm.get("label"), arm_index))
                arms.append(Arm(aid, sid, str(arm.get("role", "")), str(arm.get("label", "")), dict(arm.get("attributes", {})), self._locations(arm.get("evidence", []), sid, errors)))
            study_outcomes = []
            for outcome_index, raw_outcome in enumerate(raw.get("outcomes", [])):
                oid = str(raw_outcome.get("outcome_id") or stable_id("outcome", sid, raw_outcome.get("domain"), outcome_index))
                locations = self._locations(raw_outcome.get("evidence", []), sid, errors)
                outcome = Outcome(oid, sid, str(raw_outcome.get("domain", "not_reported")), str(raw_outcome.get("direction", "unclear")), raw_outcome.get("value"), locations)
                outcomes.append(outcome)
                study_outcomes.append(oid)
            studies.append(Study(sid, pub_id, label, fields, arms, study_outcomes, evidence, bool(raw.get("manually_revised", False))))
            publication.study_ids.append(sid)
        output = self.project.root / "extraction"
        append_jsonl(output / "publications.jsonl", [{"schema_version": "1.0", **asdict(publication)}])
        append_jsonl(output / "studies.jsonl", ({"schema_version": "1.0", **asdict(item)} for item in studies))
        append_jsonl(output / "outcomes.jsonl", ({"schema_version": "1.0", **asdict(item)} for item in outcomes))
        if errors:
            append_jsonl(output / "extraction_errors.jsonl", errors)
        else:
            (output / "extraction_errors.jsonl").touch(exist_ok=True)
        return publication, studies, outcomes

    def _evidence_map(self, raw: Any, sid: str, errors: list[dict[str, Any]]) -> dict[str, list[EvidenceLocation]]:
        if not isinstance(raw, dict):
            return {}
        return {str(name): self._locations(locations, sid, errors) for name, locations in raw.items()}

    def _locations(self, raw: Any, sid: str, errors: list[dict[str, Any]]) -> list[EvidenceLocation]:
        values = raw if isinstance(raw, list) else []
        result = []
        for item in values:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote", ""))
            valid = self.quote_validator(quote)
            status = "passed" if valid else "failed"
            if not valid:
                errors.append({"schema_version": "1.0", "study_id": sid, "error": "quote_verification_failed", "quote": quote})
            result.append(EvidenceLocation(quote, str(item.get("page", "")), str(item.get("section", "")), str(item.get("figure", "")), str(item.get("table", "")), status))
        return result
