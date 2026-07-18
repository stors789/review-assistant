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
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for event in self.history(stage=stage):
            latest[(event["record_id"], event["stage"])] = event
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
