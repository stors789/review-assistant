"""Configurable evidence matrix, contradiction, and heterogeneity analysis."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_text, load_yaml, read_jsonl, write_json
from .project import ReviewProject
from .eligibility import resolve_eligible_studies

EFFECT_DIRECTIONS = {"increase", "decrease", "no_change", "mixed", "unclear"}
SUPPORT_RELATIONS = {"supports", "contradicts", "neutral", "mixed", "unclear"}


def _get(value: Any, path: str, default: Any = "not_reported") -> Any:
    if isinstance(value, dict) and path in value:
        return value[path]
    current = value
    parts = path.split(".")
    for index, part in enumerate(parts):
        if not isinstance(current, dict):
            return default
        remainder = ".".join(parts[index:])
        if remainder in current:
            return current[remainder]
        if part not in current:
            return default
        current = current[part]
    return current


def _display(value: Any) -> str:
    if value is None or value == "":
        return "not_reported"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


class EvidenceMatrixBuilder:
    def __init__(self, project: ReviewProject):
        self.project = project
        self.schema = project.extraction_schema.data

    def build(self, row_mode: str = "study") -> list[dict[str, Any]]:
        if row_mode not in {"study", "study_comparison"}:
            raise ValueError("row_mode must be study or study_comparison")
        publications = {item["publication_id"]: item for item in _latest(read_jsonl(self.project.root / "extraction" / "publications.jsonl"), "publication_id") if item.get("status", "active") == "active"}
        studies = _latest(read_jsonl(self.project.root / "extraction" / "studies.jsonl"), "study_id")
        eligible = set(resolve_eligible_studies(self.project))
        studies = [study for study in studies if study["study_id"] in eligible]
        outcomes_by_study: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for outcome in _latest(read_jsonl(self.project.root / "extraction" / "outcomes.jsonl"), "outcome_id"):
            outcomes_by_study[outcome["study_id"]].append(outcome)
        columns = list(self.schema.get("matrix", {}).get("columns", []))
        if not columns:
            columns = ["publication_id", "study_id", *[name for name in self.schema.get("fields", {}) if name != "outcomes"], "outcome_domains", "effect_directions", "support_relations", "quote_validation_status"]
        if row_mode == "study_comparison" and "comparison_id" not in columns:
            columns.insert(columns.index("study_id") + 1 if "study_id" in columns else 0, "comparison_id")
        rows: list[dict[str, Any]] = []
        for study in sorted(studies, key=lambda item: item["study_id"]):
            arms = study.get("arms", [])
            comparisons = _comparisons(arms) if row_mode == "study_comparison" else [None]
            for comparison in comparisons or [None]:
                outcomes = outcomes_by_study.get(study["study_id"], [])
                context = {
                    **study.get("fields", {}), **study,
                    "publication": publications.get(study["publication_id"], {}),
                    "outcome_domains": sorted({item.get("domain", "not_reported") for item in outcomes}),
                    "effect_directions": sorted({item.get("effect_direction", item.get("direction", "unclear")) for item in outcomes}),
                    "support_relations": sorted({item.get("support_relation", "unclear") for item in outcomes}),
                    "quote_validation_status": sorted({location.get("validation_status", "pending") for item in outcomes for location in item.get("evidence", [])}) or ["not_reported"],
                    "comparison_id": comparison or "not_applicable",
                }
                rows.append({column: _display(_get(context, column)) for column in columns})
        output = self.project.root / "evidence"
        write_json(output / "evidence_matrix.json", {"schema_version": "1.0", "columns": columns, "rows": rows})
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_text(output / "evidence_matrix.csv", buffer.getvalue())
        return rows


def _latest(values: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged = {item[key]: item for item in values}
    return [item for item in merged.values() if item.get("status", "active") == "active"]


def _comparisons(arms: list[dict[str, Any]]) -> list[str]:
    experimental = [arm for arm in arms if arm.get("role") not in {"comparator", "control"}]
    comparators = [arm for arm in arms if arm.get("role") in {"comparator", "control"}]
    return [f"{left['arm_id']}__vs__{right['arm_id']}" for left in experimental for right in comparators]


class ContradictionAnalyzer:
    def __init__(self, project: ReviewProject):
        self.project = project
        self.settings = project.extraction_schema.data.get("contradiction_analysis", {})

    def analyze(self) -> list[dict[str, Any]]:
        studies = {item["study_id"]: item for item in _latest(read_jsonl(self.project.root / "extraction" / "studies.jsonl"), "study_id")}
        eligible = set(resolve_eligible_studies(self.project))
        studies = {sid: study for sid, study in studies.items() if sid in eligible}
        outcomes = _latest(read_jsonl(self.project.root / "extraction" / "outcomes.jsonl"), "outcome_id")
        outcomes = [outcome for outcome in outcomes if outcome.get("study_id") in eligible]
        dimensions = list(self.settings.get("claim_dimensions", ["outcome.domain"]))
        moderators = list(self.settings.get("moderator_candidates", []))
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for outcome in outcomes:
            study = studies.get(outcome["study_id"], {})
            study_context = {**study.get("fields", {}), **study}
            for field_name, value in study.get("fields", {}).items():
                if field_name.startswith("study."):
                    study_context.setdefault(field_name.split(".", 1)[1], value)
            context = {"outcome": outcome, "study": study_context}
            key = tuple(_display(_get(context, dimension)) for dimension in dimensions)
            groups[key].append({
                "study_id": outcome["study_id"], "outcome_id": outcome["outcome_id"],
                "effect_direction": outcome.get("effect_direction", outcome.get("direction", "unclear")),
                "support_relation": outcome.get("support_relation", "unclear"), "context": context,
            })
        results = []
        for key in sorted(groups):
            evidence = groups[key]
            directions = {item["effect_direction"] for item in evidence}
            relations = {item["support_relation"] for item in evidence}
            candidate_values = {}
            for moderator in moderators:
                values = sorted({_display(_get(item["context"], moderator)) for item in evidence})
                if len(values) > 1:
                    candidate_values[moderator] = values
            results.append({
                "group_id": "|".join(key), "dimensions": dict(zip(dimensions, key)),
                "study_ids": sorted({item["study_id"] for item in evidence}),
                "effect_directions": sorted(directions), "support_relations": sorted(relations),
                "directions": sorted(directions),
                "has_directional_inconsistency": _inconsistent(relations),
                "moderator_candidates": candidate_values,
                "interpretation": "candidate_explanation_only" if candidate_values else "observation_only",
            })
        output = self.project.root / "evidence"
        write_json(output / "contradiction_groups.json", {"schema_version": "1.0", "groups": results})
        lines = ["# Heterogeneity report", "", "Candidate moderators are descriptive only and are not asserted to explain differences.", ""]
        for group in results:
            lines.extend([f"## {group['group_id']}", "", f"Studies: {', '.join(group['study_ids']) or 'none'}", f"Effect directions: {', '.join(group['effect_directions']) or 'none'}", f"Support relations: {', '.join(group['support_relations']) or 'none'}", f"Inconsistent: {'yes' if group['has_directional_inconsistency'] else 'no'}", f"Candidate moderators: {_display(group['moderator_candidates'])}", ""])
        atomic_write_text(output / "heterogeneity_report.md", "\n".join(lines))
        return results


def _inconsistent(relations: set[str]) -> bool:
    observed = relations - {"unclear"}
    if "mixed" in observed:
        return True
    return "supports" in observed and "contradicts" in observed
