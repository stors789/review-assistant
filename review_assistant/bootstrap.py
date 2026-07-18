"""Create explicitly unconfirmed Review candidates from Explore artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .io_utils import write_yaml
from .project import ReviewProject


def bootstrap_from_explore(source: Path, output: Path, template: str = "generic-structured-review") -> ReviewProject:
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"Explore output does not exist: {source}")
    project = ReviewProject.initialize_review(output, template)
    outline_path = source / "outline.json"
    outline = json.loads(outline_path.read_text(encoding="utf-8")) if outline_path.exists() else {}
    meta_path = source / "outline.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    findings = []
    findings_dir = source / "findings"
    if findings_dir.exists():
        for path in sorted(findings_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                findings.extend(payload.get("findings", []) if isinstance(payload, dict) else [])
            except (ValueError, OSError):
                continue
    populations = _values(findings, ("context", "sample_or_system"))
    interventions = _values(findings, ("relation", "subject"))
    outcomes = _values(findings, ("relation", "object"))
    variables = sorted({str(item.get("name")) for finding in findings for item in finding.get("variables", []) if isinstance(item, dict) and item.get("name")})
    sections = _outline_titles(outline.get("sections", []))
    question = str(meta.get("question", ""))
    candidates = {
        "schema_version": "1.0", "confirmation_status": "unconfirmed",
        "warning": "Candidates are derived from exploratory outputs and are not a formal protocol until reviewed and copied into protocol.yaml.",
        "primary_question": question, "secondary_questions": [],
        "search_terms": _terms(question), "populations": populations,
        "interventions_or_exposures": interventions, "outcomes": outcomes,
        "seed_papers": sorted({str(finding.get("paper_id") or finding.get("title") or "") for finding in findings if finding.get("paper_id") or finding.get("title")}),
        "potential_exclusion_concepts": [], "candidate_sections": sections,
        "candidate_extraction_fields": variables,
    }
    write_yaml(project.root / "bootstrap_candidates.yaml", candidates)
    project.metadata["bootstrap_source"] = str(source)
    write_yaml(project.root / "project.yaml", project.metadata)
    return project


def _values(findings: list[dict[str, Any]], path: tuple[str, str]) -> list[str]:
    values = set()
    for finding in findings:
        parent = finding.get(path[0], {})
        value = parent.get(path[1]) if isinstance(parent, dict) else None
        if value:
            values.add(str(value))
    return sorted(values)


def _terms(question: str) -> list[str]:
    return sorted(set(re.findall(r"[\w-]{3,}", question, flags=re.UNICODE)))


def _outline_titles(sections: list[dict[str, Any]]) -> list[str]:
    result = []
    for section in sections:
        if section.get("heading"):
            result.append(str(section["heading"]))
        result.extend(_outline_titles(section.get("subsections", [])))
    return result
