"""Publication/study hierarchy and schema-driven structured extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
import re
import unicodedata

from .io_utils import append_jsonl, read_jsonl, stable_id
from .project import ReviewProject, utc_now
from .review_search import _doi, _title_key


@dataclass
class EvidenceLocation:
    quote: str
    page: str = ""
    section: str = ""
    figure: str = ""
    table: str = ""
    validation_status: str = "unverified"
    validation_method: str = "none"
    matched_excerpt: str = ""
    confidence: float = 0.0


@dataclass
class Outcome:
    outcome_id: str
    study_id: str
    domain: str
    effect_direction: str
    support_relation: str = "unclear"
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

    def __init__(self, project: ReviewProject, quote_validator: Callable[[str], Any] | None = None):
        self.project = project
        self.schema = project.extraction_schema
        self.quote_validator = quote_validator

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
        output = self.project.root / "extraction"
        for index, raw in enumerate(study_data):
            label = str(raw.get("label") or f"study-{index + 1}")
            sid = str(raw.get("study_id") or study_id(pub_id, label, index))
            fields = self.schema.apply_study_missing_values(dict(raw.get("fields", {})))
            for validation_error in self.schema.validate_study_values(fields):
                errors.append({"schema_version": "1.0", "study_id": sid, "error": "schema_validation_failed", "field": validation_error.get("field"), "validation_error": validation_error.get("error")})
            evidence = self._evidence_map(raw.get("evidence", {}), sid, errors)
            arms = []
            for arm_index, arm in enumerate(raw.get("arms", [])):
                aid = str(arm.get("arm_id") or stable_id("arm", sid, arm.get("role"), arm.get("label"), arm_index))
                arms.append(Arm(aid, sid, str(arm.get("role", "")), str(arm.get("label", "")), dict(arm.get("attributes", {})), self._locations(arm.get("evidence", []), sid, errors)))
            study_outcomes = []
            for outcome_index, raw_outcome in enumerate(raw.get("outcomes", [])):
                oid = str(raw_outcome.get("outcome_id") or stable_id("outcome", sid, raw_outcome.get("domain"), outcome_index))
                locations = self._locations(raw_outcome.get("evidence", []), sid, errors)
                used_legacy_direction = "effect_direction" not in raw_outcome and "direction" in raw_outcome
                effect_direction = str(raw_outcome.get("effect_direction", raw_outcome.get("direction", "unclear")))
                support_relation = str(raw_outcome.get("support_relation") or self._configured_support_relation(str(raw_outcome.get("domain", "not_reported")), effect_direction))
                normalized_outcome = {
                    **raw_outcome, "domain": str(raw_outcome.get("domain", "not_reported")),
                    "effect_direction": effect_direction, "support_relation": support_relation,
                    "evidence": [asdict(location) for location in locations],
                }
                normalized_outcome.pop("direction", None)
                for validation_error in self.schema.validate_outcome(normalized_outcome):
                    errors.append({"schema_version": "1.0", "study_id": sid, "outcome_id": oid, "error": "outcome_schema_validation_failed", "field": validation_error.get("field"), "validation_error": validation_error.get("error"), "raw_record": raw_outcome})
                if used_legacy_direction:
                    append_jsonl(output / "extraction_warnings.jsonl", [{
                        "schema_version": "1.0", "study_id": sid, "outcome_id": oid,
                        "warning": "legacy_direction_migrated", "detail": "direction was migrated to effect_direction; support_relation was not inferred without configuration",
                    }])
                outcome = Outcome(oid, sid, normalized_outcome["domain"], effect_direction, support_relation, raw_outcome.get("value"), locations)
                outcomes.append(outcome)
                study_outcomes.append(oid)
            studies.append(Study(sid, pub_id, label, fields, arms, study_outcomes, evidence, bool(raw.get("manually_revised", False))))
            publication.study_ids.append(sid)
        append_jsonl(output / "publications.jsonl", [{"schema_version": "1.0", **asdict(publication)}])
        append_jsonl(output / "studies.jsonl", ({"schema_version": "1.0", **asdict(item)} for item in studies))
        append_jsonl(output / "outcomes.jsonl", ({"schema_version": "1.0", **asdict(item)} for item in outcomes))
        self._persist_links(record, publication, studies)
        if errors:
            append_jsonl(output / "extraction_errors.jsonl", errors)
        else:
            (output / "extraction_errors.jsonl").touch(exist_ok=True)
        return publication, studies, outcomes

    def _configured_support_relation(self, domain: str, effect_direction: str) -> str:
        spec = self.schema.data.get("outcome_domains", {}).get(domain, {})
        beneficial = spec.get("beneficial_direction") if isinstance(spec, dict) else None
        if beneficial not in {"increase", "decrease"}:
            return "unclear"
        if effect_direction == beneficial:
            return "supports"
        if effect_direction in {"increase", "decrease"}:
            return "contradicts"
        if effect_direction == "no_change":
            return "neutral"
        if effect_direction == "mixed":
            return "mixed"
        return "unclear"

    def _persist_links(self, record: dict[str, Any], publication: Publication, studies: list[Study]) -> None:
        source_record_id, method, confidence = match_extraction_record(self.project, record)
        common = {
            "schema_version": "1.0", "record_id": source_record_id,
            "publication_id": publication.publication_id,
            "source_file": str(record.get("source_file", "")), "link_method": method,
            "confidence": confidence, "timestamp": utc_now(),
            "protocol_hash": self.project.track_protocol(),
        }
        append_jsonl(self.project.root / "extraction" / "record_publication_links.jsonl", [common])
        append_jsonl(self.project.root / "extraction" / "study_record_links.jsonl", [
            {**common, "study_id": study.study_id} for study in studies
        ])
        if not source_record_id:
            append_jsonl(self.project.root / "extraction" / "extraction_errors.jsonl", [{
                "schema_version": "1.0", "publication_id": publication.publication_id,
                "study_ids": [study.study_id for study in studies], "source_file": common["source_file"],
                "error": "record_link_unresolved",
            }])

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
            if item.get("manual_verified") is True:
                validation = {"validation_status": "passed", "validation_method": "manual", "matched_excerpt": quote, "confidence": 1.0}
            elif self.quote_validator is None:
                validation = {"validation_status": "unverified", "validation_method": "none", "matched_excerpt": "", "confidence": 0.0}
            else:
                validated = self.quote_validator(quote)
                validation = validated if isinstance(validated, dict) else {
                    "validation_status": "passed" if validated else "failed",
                    "validation_method": "manual", "matched_excerpt": quote if validated else "",
                    "confidence": 1.0 if validated else 0.0,
                }
            status = validation["validation_status"]
            if status == "failed":
                errors.append({"schema_version": "1.0", "study_id": sid, "error": "quote_verification_failed", "quote": quote})
            result.append(EvidenceLocation(
                quote, str(item.get("page", "")), str(item.get("section", "")),
                str(item.get("figure", "")), str(item.get("table", "")), status,
                str(validation.get("validation_method", "none")), str(validation.get("matched_excerpt", "")),
                float(validation.get("confidence", 0.0)),
            ))
        return result


def extract_fulltext_documents(
    project: ReviewProject,
    pdf_paths: list[Path],
    *,
    model: str,
    extractor: Callable[[str, dict[str, Any], Path], dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Extract Review records from PDFs using the external schema and EvidencePack.

    ``extractor`` is injectable for offline tests and alternate providers. The
    default implementation reuses the shared PDF, EvidencePack, and LLM layers.
    """
    from .utils import extract_pdf_text

    store = StudyExtractionStore(project)
    completed = 0
    failed = 0
    for pdf_path in pdf_paths:
        try:
            source_record_id, _, _ = match_extraction_record(project, {
                "source_file": pdf_path.name, "publication": {"title": pdf_path.stem},
            })
            from .eligibility import latest_screening_decisions, screening_enforcement
            decisions = latest_screening_decisions(project)
            fulltext = decisions.get((source_record_id, "fulltext")) if source_record_id else None
            enforcement = screening_enforcement(project)
            if fulltext and fulltext.get("decision") != "include":
                append_jsonl(project.root / "extraction" / "extraction_errors.jsonl", [{
                    "schema_version": "1.0", "file": str(pdf_path), "record_id": source_record_id,
                    "error": "document_skipped_ineligible", "decision": fulltext.get("decision"),
                }])
                continue
            if enforcement == "required" and (not source_record_id or not fulltext or fulltext.get("decision") != "include"):
                raise ValueError("screening is required and the PDF is not bound to a full-text included record")
            full_text = extract_pdf_text(pdf_path)
            pack, coverage = build_schema_evidence_pack(full_text, store.schema.data)
            if extractor is None:
                payload = _llm_extract_record(pack, store.schema.data, pdf_path, model)
            else:
                payload = extractor(pack, store.schema.data, pdf_path)
            if not isinstance(payload, dict):
                raise ValueError("extractor must return a JSON object")
            payload.setdefault("publication", {})
            payload["publication"].setdefault("title", pdf_path.stem)
            payload.setdefault("source_file", pdf_path.name)
            if source_record_id:
                payload.setdefault("source_record_id", source_record_id)
            quote_validator = lambda quote, text=full_text: validate_quote_in_text(quote, text)
            StudyExtractionStore(project, quote_validator=quote_validator).ingest(payload)
            completed += 1
        except Exception as exc:
            append_jsonl(project.root / "extraction" / "extraction_errors.jsonl", [{
                "schema_version": "1.0", "file": str(pdf_path),
                "error": "document_extraction_failed", "detail": f"{type(exc).__name__}: {exc}",
            }])
            failed += 1
    return {"completed": completed, "failed": failed}


def match_extraction_record(project: ReviewProject, extraction: dict[str, Any]) -> tuple[str, str, str]:
    """Match without fuzzy guessing, in explicit/DOI/PMID/title certainty order."""
    records = read_jsonl(project.root / "search" / "deduplicated_records.jsonl")
    by_id = {str(item.get("record_id")): item for item in records if item.get("record_id")}
    explicit = str(extraction.get("source_record_id", "")).strip()
    if explicit:
        return explicit, "explicit_input", "exact"
    publication = extraction.get("publication", {}) if isinstance(extraction.get("publication"), dict) else {}
    source_file = Path(str(extraction.get("source_file", ""))).stem
    target_doi = _doi(publication)
    if target_doi:
        matches = [rid for rid, item in by_id.items() if _doi(item) == target_doi]
        if len(matches) == 1:
            return matches[0], "doi", "exact"
    target_pmid = str(publication.get("pmid", "")).strip()
    if target_pmid:
        matches = [rid for rid, item in by_id.items() if str(item.get("pmid", "")).strip() == target_pmid]
        if len(matches) == 1:
            return matches[0], "pmid", "exact"
    target_title = _title_key(publication) or _title_key({"title": source_file})
    if target_title:
        matches = [rid for rid, item in by_id.items() if _title_key(item) == target_title]
        if len(matches) == 1:
            return matches[0], "title", "exact"
    for rid, item in by_id.items():
        configured_file = item.get("source_file") or item.get("full_text_file") or item.get("file")
        if configured_file and Path(str(configured_file)).name == Path(str(extraction.get("source_file", ""))).name:
            return rid, "manual", "manual"
    return "", "unlinked", "probable"


def _llm_extract_record(pack: str, schema: dict[str, Any], pdf_path: Path, model: str) -> dict[str, Any]:
    import json
    from . import llm_client

    system = (
        "Extract publication- and study-level evidence strictly from the supplied text. "
        "Follow the external schema. Never fill absent information from general knowledge; "
        "use each field's missing_value or not_reported, and use unclear for uncertain directions. "
        "Every material outcome must include an exact evidence quote and location when reported. "
        "Return one JSON object with publication and studies; each study may contain fields, arms, outcomes, and evidence."
    )
    user = (
        f"Source file: {pdf_path.name}\n\nExtraction schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)}\n\nEvidencePack:\n{pack}"
    )
    result = llm_client.call_json(llm_client.get_client(), system, user, model, max_tokens=16384)
    if not isinstance(result, dict):
        raise ValueError("LLM extraction response must be an object")
    return result


def validate_quote_in_text(quote: str, text: str, min_length: int = 12) -> dict[str, Any]:
    quote = quote.strip()
    if len(quote) < min_length:
        return {"validation_status": "failed", "validation_method": "none", "matched_excerpt": "", "confidence": 0.0}
    if quote in text:
        return {"validation_status": "passed", "validation_method": "exact", "matched_excerpt": quote, "confidence": 1.0}

    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFKC", value)
        value = value.replace("ﬁ", "fi").replace("ﬂ", "fl")
        value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value)
        value = re.sub(r"[\s\W_]+", " ", value, flags=re.UNICODE)
        return value.casefold().strip()

    normalized_quote = normalize(quote)
    normalized_text = normalize(text)
    if len(normalized_quote) >= min_length and normalized_quote in normalized_text:
        return {"validation_status": "passed", "validation_method": "normalized", "matched_excerpt": quote, "confidence": 0.9}
    return {"validation_status": "failed", "validation_method": "normalized", "matched_excerpt": "", "confidence": 0.0}


def _quote_in_text(quote: str, text: str) -> bool:
    """Compatibility boolean wrapper."""
    return validate_quote_in_text(quote, text)["validation_status"] == "passed"
