"""Auditable multi-source search orchestration for Review projects."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .io_utils import append_jsonl, load_yaml, read_jsonl, stable_id, write_json
from .project import ReviewProject

SearchRunner = Callable[[str, int], list[dict[str, Any]]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doi(record: dict[str, Any]) -> str:
    external = record.get("externalIds") or record.get("external_ids") or {}
    value = record.get("doi") or external.get("DOI") or external.get("doi") or ""
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value).strip().lower())


def _title_key(record: dict[str, Any]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(record.get("title", "")).lower()).strip()


def record_key(record: dict[str, Any]) -> str:
    doi = _doi(record)
    return f"doi:{doi}" if doi else f"title:{_title_key(record)}"


@dataclass
class SearchRunResult:
    records: list[dict[str, Any]]
    logs: list[dict[str, Any]]
    failures: int


class SearchOrchestrator:
    def __init__(self, project: ReviewProject, runners: dict[str, SearchRunner], tool_version: str = "unknown"):
        self.project = project
        self.runners = runners
        self.tool_version = tool_version

    def run(self, search_id: str | None = None, limit: int = 100) -> SearchRunResult:
        plan = load_yaml(self.project.root / "search_plan.yaml")
        searches = plan.get("searches", [])
        if not isinstance(searches, list):
            raise ValueError("search_plan.searches must be a list")
        selected = [item for item in searches if item.get("enabled", True) and (search_id is None or item.get("id") == search_id)]
        if search_id and not selected:
            raise ValueError(f"Enabled search {search_id!r} was not found")

        raw: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        failures = 0
        protocol_hash = self.project.track_protocol()
        for spec in selected:
            started_at = _now()
            started = time.monotonic()
            error = None
            records: list[dict[str, Any]] = []
            source = str(spec.get("source", ""))
            query = str(spec.get("query", ""))
            try:
                if not query:
                    raise ValueError("query must not be empty")
                if source not in self.runners:
                    raise ValueError(f"No search runner registered for source {source!r}")
                records = self.runners[source](query, limit)
                if not isinstance(records, list):
                    raise TypeError("search runner must return a list")
            except Exception as exc:  # per-search failure is persisted and does not erase other paths
                error = f"{type(exc).__name__}: {exc}"
                failures += 1
            run_token = stable_id("searchrun", spec.get("id"), started_at)
            for record in records:
                enriched = dict(record)
                enriched["search_provenance"] = [{"search_id": spec.get("id"), "source": source, "query": query, "run_id": run_token}]
                enriched["raw_record_id"] = stable_id("record", spec.get("id"), record_key(record), len(raw))
                raw.append(enriched)
            logs.append({
                "schema_version": "1.0", "run_id": run_token, "search_id": spec.get("id"),
                "source": source, "exact_query": query, "execution_date": started_at,
                "execution_time": started_at, "tool_api_version": spec.get("api_version", self.tool_version),
                "raw_count": len(records), "accepted_count": len(records), "error": error,
                "duration_seconds": round(time.monotonic() - started, 6), "protocol_hash": protocol_hash,
            })

        output = self.project.root / "search"
        append_jsonl(output / "search_log.jsonl", logs)
        append_jsonl(output / "raw_records.jsonl", raw)
        all_raw = read_jsonl(output / "raw_records.jsonl")
        deduped, groups = deduplicate_records(all_raw)
        _overwrite_jsonl(output / "deduplicated_records.jsonl", deduped)
        write_json(output / "duplicate_groups.json", groups)
        summary = ["# Search summary", "", f"Protocol hash: `{protocol_hash}`", "", f"Raw records: {len(all_raw)}", f"Unique records: {len(deduped)}", f"Failures in this run: {failures}", ""]
        from .io_utils import atomic_write_text
        atomic_write_text(output / "search_summary.md", "\n".join(summary))
        return SearchRunResult(records=deduped, logs=logs, failures=failures)


def _overwrite_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    from .io_utils import atomic_write_text
    text = "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values)
    atomic_write_text(path, text)


def deduplicate_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    merged: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = {}
    for record in records:
        key = record_key(record)
        if not key or key == "title:":
            key = f"raw:{record.get('raw_record_id', stable_id('record', len(merged)))}"
        if key not in merged:
            merged[key] = dict(record)
            merged[key]["record_id"] = stable_id("record", key)
            merged[key]["search_provenance"] = list(record.get("search_provenance", []))
            members[key] = []
        else:
            known = {(p.get("run_id"), p.get("search_id")) for p in merged[key]["search_provenance"]}
            merged[key]["search_provenance"].extend(p for p in record.get("search_provenance", []) if (p.get("run_id"), p.get("search_id")) not in known)
            for field, value in record.items():
                if not merged[key].get(field) and value:
                    merged[key][field] = value
        members[key].append(str(record.get("raw_record_id", "")))
    ordered = [merged[key] for key in sorted(merged)]
    duplicate_groups = {merged[key]["record_id"]: ids for key, ids in members.items() if len(ids) > 1}
    return ordered, duplicate_groups
