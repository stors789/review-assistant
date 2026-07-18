"""Protocol-driven synthesis plans, lossless evidence memos, and claim maps."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .io_utils import atomic_write_text, load_yaml, read_jsonl, stable_id, write_json
from .project import ReviewProject


def eligible_study_ids(project: ReviewProject) -> list[str]:
    studies = {item["study_id"] for item in read_jsonl(project.root / "extraction" / "studies.jsonl")}
    history = read_jsonl(project.root / "screening" / "decision_history.jsonl")
    latest = {}
    for item in history:
        if item.get("stage") == "fulltext":
            latest[item["record_id"]] = item
    included = {record_id for record_id, item in latest.items() if item.get("decision") == "include"}
    linked = {item.get("study_id") for item in read_jsonl(project.root / "extraction" / "study_record_links.jsonl") if item.get("record_id") in included}
    return sorted(studies & linked) if latest and linked else sorted(studies)


def resolve_synthesis_plan(project: ReviewProject) -> dict[str, Any]:
    configured = load_yaml(project.root / "synthesis_plan.yaml")
    protocol = project.protocol.data
    configured_sections = configured.get("sections", [])
    by_title = {str(item.get("title", "")): item for item in configured_sections if isinstance(item, dict)}
    required = protocol.get("synthesis", {}).get("required_sections", [])
    section_specs: list[dict[str, Any]] = []
    for index, raw in enumerate(required):
        item = {"title": raw} if isinstance(raw, str) else dict(raw)
        item = {**by_title.get(str(item.get("title", "")), {}), **item}
        item.setdefault("section_id", f"S{index + 1:02d}")
        section_specs.append(item)
    known_titles = {item.get("title") for item in section_specs}
    for raw in configured_sections:
        if raw.get("title") not in known_titles:
            item = dict(raw)
            item.setdefault("section_id", f"S{len(section_specs) + 1:02d}")
            section_specs.append(item)
    all_studies = eligible_study_ids(project)
    outcomes = read_jsonl(project.root / "extraction" / "outcomes.jsonl")
    directions: dict[str, list[str]] = defaultdict(list)
    for outcome in outcomes:
        directions[outcome["study_id"]].append(outcome.get("direction", "unclear"))
    resolved = []
    for spec in section_specs:
        included = spec.get("included_study_ids")
        study_ids = sorted(set(included) & set(all_studies)) if isinstance(included, list) else list(all_studies)
        direction_sets = {name: sorted(sid for sid in study_ids if name in directions.get(sid, [])) for name in ("increase", "decrease", "no_change", "mixed", "unclear")}
        resolved.append({
            "section_id": spec["section_id"], "title": str(spec.get("title", "")),
            "required_questions": list(spec.get("required_questions", protocol.get("synthesis", {}).get("required_questions", []))),
            "included_study_ids": study_ids, "positive_evidence": direction_sets["increase"],
            "negative_evidence": direction_sets["decrease"], "no_change_evidence": direction_sets["no_change"],
            "mixed_evidence": direction_sets["mixed"], "unclear_evidence": direction_sets["unclear"],
            "required_qualifiers": list(spec.get("required_qualifiers", [])),
            "missing_evidence": [] if study_ids else ["evidence_insufficient"],
            "evidence_memo_dependencies": [],
        })
    result = {"schema_version": "1.0", "protocol_hash": project.track_protocol(), "sections": resolved, "settings": configured.get("settings", {})}
    write_json(project.root / "synthesis" / "resolved_synthesis_plan.json", result)
    return result


def build_evidence_memos(project: ReviewProject, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    plan = plan or resolve_synthesis_plan(project)
    studies = {item["study_id"]: item for item in read_jsonl(project.root / "extraction" / "studies.jsonl")}
    outcomes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in read_jsonl(project.root / "extraction" / "outcomes.jsonl"):
        outcomes[item["study_id"]].append(item)
    batch_size = int(plan.get("settings", {}).get("evidence_batch_size", 25))
    if batch_size < 1:
        raise ValueError("evidence_batch_size must be positive")
    memo_dir = project.root / "evidence" / "evidence_memos"
    memo_dir.mkdir(parents=True, exist_ok=True)
    all_memos = []
    for section in plan["sections"]:
        ids = section["included_study_ids"]
        dependencies = []
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset:offset + batch_size]
            memo_id = f"{section['section_id']}_batch_{offset // batch_size + 1:03d}"
            items = [{"study_id": sid, "publication_id": studies[sid]["publication_id"], "fields": studies[sid].get("fields", {}), "outcomes": outcomes.get(sid, [])} for sid in batch]
            memo = {"schema_version": "1.0", "memo_id": memo_id, "section_id": section["section_id"], "study_ids": batch, "evidence_items": items}
            write_json(memo_dir / f"{memo_id}.json", memo)
            lines = [f"# Evidence memo {memo_id}", "", f"Studies: {', '.join(batch)}", ""]
            for item in items:
                lines.extend([f"## {item['study_id']}", "", f"Publication: {item['publication_id']}", f"Outcomes: {json.dumps(item['outcomes'], ensure_ascii=False, sort_keys=True)}", ""])
            atomic_write_text(memo_dir / f"{memo_id}.md", "\n".join(lines))
            dependencies.append(memo_id)
            all_memos.append(memo)
        section["evidence_memo_dependencies"] = dependencies
        write_json(memo_dir / f"{section['section_id']}_summary.json", {"schema_version": "1.0", "section_id": section["section_id"], "memo_ids": dependencies, "all_study_ids": ids, "evidence_count": len(ids)})
    write_json(project.root / "synthesis" / "resolved_synthesis_plan.json", plan)
    return all_memos


def write_section(section_spec: dict[str, Any], evidence_bundle: list[dict[str, Any]], synthesis_context: dict[str, Any], mode: str, writer: Callable[..., str] | None = None) -> str:
    if mode not in {"explore", "review"}:
        raise ValueError("mode must be explore or review")
    if writer:
        return writer(section_spec=section_spec, evidence_bundle=evidence_bundle, synthesis_context=synthesis_context, mode=mode)
    if mode == "review" and not section_spec.get("included_study_ids"):
        return "Evidence is insufficient for this protocol-required section."
    ids = section_spec.get("included_study_ids", [])
    direction_labels = []
    for field, label in (("positive_evidence", "supporting"), ("negative_evidence", "opposing"), ("no_change_evidence", "no-change"), ("mixed_evidence", "mixed")):
        values = section_spec.get(field, [])
        if values:
            direction_labels.append(f"{label}: {', '.join(values)}")
    return f"Evidence from {len(ids)} eligible studies was synthesized ({'; '.join(direction_labels) or 'direction unclear'}). " + " ".join(f"[{sid}]" for sid in ids)


def synthesize_review(project: ReviewProject, writer: Callable[..., str] | None = None) -> str:
    plan = resolve_synthesis_plan(project)
    memos = build_evidence_memos(project, plan)
    by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for memo in memos:
        by_section[memo["section_id"]].append(memo)
    protocol = project.protocol.data
    title = protocol["review"].get("title") or "Review synthesis"
    sections = [f"# {title}", "", f"Protocol hash: `{plan['protocol_hash']}`", ""]
    draft_dir = project.root / "synthesis" / "section_drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    for spec in plan["sections"]:
        content = write_section(spec, by_section.get(spec["section_id"], []), {"protocol": protocol}, "review", writer)
        atomic_write_text(draft_dir / f"{spec['section_id']}.md", content + "\n")
        sections.extend([f"## {spec['title']}", "", content, ""])
    report = "\n".join(sections).rstrip() + "\n"
    atomic_write_text(project.root / "synthesis" / "review_draft.md", report)
    build_claim_map(project, report, plan)
    return report


def build_claim_map(project: ReviewProject, report: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan or resolve_synthesis_plan(project)
    valid_studies = set(eligible_study_ids(project))
    outcomes = read_jsonl(project.root / "extraction" / "outcomes.jsonl")
    locations = defaultdict(list)
    for outcome in outcomes:
        locations[outcome["study_id"]].extend(outcome.get("evidence", []))
    contradiction_payload = project.root / "evidence" / "contradiction_groups.json"
    contradiction_groups = json.loads(contradiction_payload.read_text())["groups"] if contradiction_payload.exists() else []
    claims = []
    current_section = ""
    title_to_id = {section["title"]: section["section_id"] for section in plan["sections"]}
    for line in report.splitlines():
        if line.startswith("## "):
            current_section = title_to_id.get(line[3:].strip(), "")
            continue
        for sentence in re.split(r"(?<=[.!?。！？])\s+", line.strip()):
            if not sentence or sentence.startswith("#") or sentence.startswith("Protocol hash:"):
                continue
            cited = sorted(set(re.findall(r"\[([^\[\]]*study_[0-9a-f]{16})\]", sentence)) & valid_studies)
            contradicting = sorted({sid for group in contradiction_groups if set(cited) & set(group.get("study_ids", [])) and group.get("has_directional_inconsistency") for sid in group.get("study_ids", []) if sid not in cited})
            support = "unsupported" if not cited else ("mixed" if contradicting else "supported")
            claims.append({
                "claim_id": stable_id("claim", current_section, sentence), "section_id": current_section,
                "sentence": sentence, "supporting_studies": cited, "contradicting_studies": contradicting,
                "support_level": support, "required_qualifiers": [],
                "evidence_locations": [{"study_id": sid, "locations": locations[sid]} for sid in cited],
                "protocol_scope_status": "inside", "audit_status": "pending",
            })
    result = {"schema_version": "1.0", "protocol_hash": project.track_protocol(), "claims": claims}
    write_json(project.root / "synthesis" / "claim_map.json", result)
    return result
