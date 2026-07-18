"""Persistent run metadata and recoverable stage status."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import append_jsonl, stable_id, write_json
from .project import ReviewProject


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class RunState:
    project: ReviewProject
    run_id: str
    root: Path
    metadata: dict[str, Any]
    stages: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, project: ReviewProject, *, provider: str = "", model: str = "") -> "RunState":
        started = now()
        run_id = stable_id("run", started, project.root.name)
        root = project.root / "runs" / run_id
        root.mkdir(parents=True, exist_ok=False)
        metadata = {
            "schema_version": "1.0", "run_id": run_id, "mode": project.mode,
            "started_at": started, "finished_at": None, "status": "running",
            "project_schema_version": project.metadata.get("schema_version"),
            "protocol_hash": project.track_protocol(), "extraction_schema_hash": project.extraction_schema.hash,
            "provider": provider, "model": model,
        }
        state = cls(project, run_id, root, metadata)
        state._flush()
        write_json(root / "inputs.json", {
            "protocol.yaml": fingerprint(project.root / "protocol.yaml"),
            "extraction_schema.yaml": fingerprint(project.root / "extraction_schema.yaml"),
            "search_plan.yaml": fingerprint(project.root / "search_plan.yaml"),
            "synthesis_plan.yaml": fingerprint(project.root / "synthesis_plan.yaml"),
        })
        write_json(root / "outputs.json", {})
        (root / "errors.jsonl").touch()
        return state

    @classmethod
    def resume_latest(cls, project: ReviewProject) -> "RunState":
        candidates = sorted((project.root / "runs").glob("run_*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for root in candidates:
            metadata_path = root / "metadata.json"
            if not metadata_path.exists():
                continue
            import json
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("status") not in {"failed", "running"}:
                continue
            status_path = root / "stage_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {"stages": {}}
            metadata.update(status="running", finished_at=None)
            state = cls(project, metadata["run_id"], root, metadata, status.get("stages", {}))
            state._flush()
            return state
        raise RuntimeError("No incomplete run is available to resume")

    def begin_stage(self, stage: str) -> None:
        self.stages[stage] = {"status": "running", "started_at": now(), "finished_at": None, "outputs": []}
        self._flush()

    def finish_stage(self, stage: str, outputs: list[str]) -> None:
        self.stages[stage].update(status="completed", finished_at=now(), outputs=outputs)
        self._flush()
        write_json(self.root / "outputs.json", {name: fingerprint(self.project.root / name) for value in self.stages.values() for name in value.get("outputs", [])})

    def fail_stage(self, stage: str, error: Exception) -> None:
        self.stages.setdefault(stage, {})
        self.stages[stage].update(status="failed", finished_at=now(), error=f"{type(error).__name__}: {error}")
        append_jsonl(self.root / "errors.jsonl", [{"timestamp": now(), "stage": stage, "error": f"{type(error).__name__}: {error}"}])
        self.metadata.update(status="failed", finished_at=now(), failure_summary=str(error))
        self._flush()

    def finish(self) -> None:
        self.metadata.update(status="completed", finished_at=now())
        self._flush()

    def _flush(self) -> None:
        write_json(self.root / "metadata.json", self.metadata)
        write_json(self.root / "stage_status.json", {"schema_version": "1.0", "stages": self.stages})
