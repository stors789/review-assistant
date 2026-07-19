"""Two-stage screening state, import, history, and PRISMA counts."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .io_utils import append_jsonl, atomic_write_text, load_yaml, read_jsonl, write_json
from .project import ReviewProject

Stage = Literal["title_abstract", "fulltext"]
Decision = Literal["include", "exclude", "uncertain", "duplicate"]
STAGES = {"title_abstract", "fulltext"}
DECISIONS = {"include", "exclude", "uncertain", "duplicate"}
CSV_FIELDS = ["record_id", "stage", "decision", "reason_code", "reviewer", "timestamp", "note", "ai_recommendation", "ai_confidence"]
COMPLETENESS_SCHEMA_VERSION = "1.0"


@dataclass
class ScreeningDecision:
    record_id: str
    stage: Stage
    decision: Decision
    reviewer: str
    timestamp: str
    reason_code: str = ""
    note: str = ""
    ai_recommendation: str = ""
    ai_confidence: str = ""


def latest_screening_decisions(project: ReviewProject) -> dict[tuple[str, str], dict[str, Any]]:
    """Return the last persisted decision for each record/stage pair.

    Decision history is append-only.  The event order, rather than a caller
    supplied timestamp, is the revision order so that an imported correction
    cannot be hidden by an older timestamp.
    """
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in read_jsonl(project.root / "screening" / "decision_history.jsonl"):
        record_id = str(item.get("record_id", ""))
        stage = str(item.get("stage", ""))
        if record_id and stage in STAGES:
            latest[(record_id, stage)] = item
    return latest


def resolve_screening_completeness(project: ReviewProject) -> dict[str, Any]:
    """Resolve screening completeness against the complete search universe.

    The deduplicated search output is the denominator.  This deliberately
    does not infer completeness from the subset of records that happen to
    have a decision-history row, which is the common source of false passes in
    formal review audits.
    """
    protocol_screening = project.protocol.data.get("screening", {})
    enforcement = str(protocol_screening.get("enforcement", "optional"))
    if enforcement not in {"required", "optional", "disabled"}:
        raise ValueError("screening.enforcement must be required, optional, or disabled")

    records = read_jsonl(project.root / "search" / "deduplicated_records.jsonl")
    universe = sorted({str(item.get("record_id")) for item in records if item.get("record_id")})
    universe_set = set(universe)
    latest = latest_screening_decisions(project)
    legal_complete = {"include", "exclude", "duplicate"}
    illegal_state_ids: set[str] = set()

    for (record_id, stage), event in latest.items():
        decision = event.get("decision")
        if record_id not in universe_set or decision not in DECISIONS:
            illegal_state_ids.add(record_id)

    title_complete: set[str] = set()
    title_missing: set[str] = set()
    title_uncertain: set[str] = set()
    title_included: set[str] = set()
    for record_id in universe:
        event = latest.get((record_id, "title_abstract"))
        if event is None:
            title_missing.add(record_id)
            continue
        decision = event.get("decision")
        if decision in legal_complete:
            title_complete.add(record_id)
            if decision == "include":
                title_included.add(record_id)
        elif decision == "uncertain":
            title_uncertain.add(record_id)
        else:
            illegal_state_ids.add(record_id)

    fulltext_required = set(title_included)
    fulltext_complete: set[str] = set()
    fulltext_missing: set[str] = set()
    fulltext_uncertain: set[str] = set()
    fulltext_included: set[str] = set()
    for record_id in sorted(fulltext_required):
        event = latest.get((record_id, "fulltext"))
        if event is None:
            fulltext_missing.add(record_id)
            continue
        decision = event.get("decision")
        if decision in legal_complete:
            fulltext_complete.add(record_id)
            if decision == "include":
                fulltext_included.add(record_id)
        elif decision == "uncertain":
            fulltext_uncertain.add(record_id)
        else:
            illegal_state_ids.add(record_id)

    # A full-text decision for a title/abstract exclusion or duplicate is an
    # impossible state.  Retain it as a diagnostic rather than silently using
    # the later decision to make the record eligible.
    for (record_id, stage), event in latest.items():
        if stage == "fulltext" and record_id in universe_set and record_id not in title_included:
            illegal_state_ids.add(record_id)

    result = {
        "schema_version": COMPLETENESS_SCHEMA_VERSION,
        "enforcement": enforcement,
        "all_search_record_ids": universe,
        "title_abstract_complete_ids": sorted(title_complete),
        "title_abstract_included_ids": sorted(title_included),
        "title_abstract_missing_ids": sorted(title_missing),
        "title_abstract_uncertain_ids": sorted(title_uncertain),
        "fulltext_required_ids": sorted(fulltext_required),
        "fulltext_complete_ids": sorted(fulltext_complete),
        "fulltext_missing_ids": sorted(fulltext_missing),
        "fulltext_uncertain_ids": sorted(fulltext_uncertain),
        "illegal_state_ids": sorted(illegal_state_ids),
        "screening_required": enforcement == "required",
    }
    write_json(project.root / "screening" / "completeness.json", result)
    return result


class ScreeningStore:
    def __init__(self, project: ReviewProject):
        self.project = project
        protocol = project.protocol.data
        self.reason_codes = set(protocol.get("screening", {}).get("reason_codes", []))

    def decide(self, record_id: str, stage: str, decision: str, reviewer: str, *, reason_code: str = "", note: str = "", ai_recommendation: str = "", ai_confidence: str = "", timestamp: str | None = None) -> ScreeningDecision:
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {sorted(STAGES)}")
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        if decision in {"exclude", "duplicate"} and not reason_code:
            raise ValueError("excluded and duplicate records require a reason_code")
        if reason_code and reason_code not in self.reason_codes:
            raise ValueError(f"reason_code {reason_code!r} is not declared by the protocol")
        if ai_recommendation and ai_recommendation not in DECISIONS:
            raise ValueError("AI recommendation is invalid")
        item = ScreeningDecision(
            record_id=record_id, stage=stage, decision=decision, reviewer=reviewer,
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(), reason_code=reason_code,
            note=note, ai_recommendation=ai_recommendation, ai_confidence=str(ai_confidence),
        )
        history = self.history(record_id=record_id, stage=stage)
        event = asdict(item)
        event["schema_version"] = "1.0"
        event["previous_decision"] = history[-1]["decision"] if history else None
        append_jsonl(self.project.root / "screening" / "decision_history.jsonl", [event])
        self._export_current()
        return item

    def history(self, *, record_id: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        events = read_jsonl(self.project.root / "screening" / "decision_history.jsonl")
        return [event for event in events if (record_id is None or event["record_id"] == record_id) and (stage is None or event["stage"] == stage)]

    def current(self, stage: str | None = None) -> list[dict[str, Any]]:
        latest = latest_screening_decisions(self.project)
        if stage is not None:
            latest = {key: value for key, value in latest.items() if key[1] == stage}
        return [latest[key] for key in sorted(latest)]

    def import_csv(self, path: Path, mapping: dict[str, str], *, default_stage: str | None = None, default_reviewer: str = "import") -> int:
        required = {"record_id", "decision"}
        if not required.issubset(mapping):
            raise ValueError(f"CSV mapping requires: {', '.join(sorted(required))}")
        count = 0
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [source for source in mapping.values() if source not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"CSV columns not found: {', '.join(missing)}")
            for row in reader:
                normalized = {target: row.get(source, "").strip() for target, source in mapping.items()}
                self.decide(
                    normalized["record_id"], normalized.get("stage") or default_stage or "title_abstract",
                    normalized["decision"].lower(), normalized.get("reviewer") or default_reviewer,
                    reason_code=normalized.get("reason_code", ""), note=normalized.get("note", ""),
                    ai_recommendation=normalized.get("ai_recommendation", ""), ai_confidence=normalized.get("ai_confidence", ""),
                    timestamp=normalized.get("timestamp") or None,
                )
                count += 1
        return count

    def _export_current(self) -> None:
        for stage in sorted(STAGES):
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.current(stage))
            atomic_write_text(self.project.root / "screening" / f"{stage}.csv", buffer.getvalue())
        counts: dict[str, Any] = {"schema_version": "1.0", "stages": {}}
        for stage in sorted(STAGES):
            items = self.current(stage)
            decisions = {name: sum(item["decision"] == name for item in items) for name in sorted(DECISIONS)}
            counts["stages"][stage] = {"total": len(items), **decisions}
        counts["identified_unique"] = len({item["record_id"] for item in self.current()})
        write_json(self.project.root / "screening" / "prisma_counts.json", counts)
