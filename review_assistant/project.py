"""Review project lifecycle, template discovery, and protocol tracking."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from .io_utils import append_jsonl, load_yaml, stable_hash, write_yaml
from .protocol import ExtractionSchema, Protocol

SCHEMA_VERSION = "1.0"
PROJECT_DIRS = ("search", "screening", "fulltext", "extraction", "evidence", "synthesis", "audit", "runs")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_templates() -> list[str]:
    root = files("review_assistant").joinpath("templates")
    return sorted(item.name for item in root.iterdir() if item.is_dir() and item.joinpath("protocol.yaml").is_file())


@dataclass
class ReviewProject:
    root: Path
    mode: Literal["explore", "review"]
    metadata: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> "ReviewProject":
        root = root.resolve()
        data = load_yaml(root / "project.yaml")
        mode = data.get("mode")
        if mode not in {"explore", "review"}:
            raise ValueError("project.mode must be 'explore' or 'review'")
        return cls(root=root, mode=mode, metadata=data)

    @classmethod
    def initialize_review(cls, root: Path, template: str = "generic-structured-review") -> "ReviewProject":
        available = discover_templates()
        if template not in available:
            raise ValueError(f"Unknown template {template!r}; available: {', '.join(available)}")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        template_root = files("review_assistant").joinpath("templates", template)
        for name in ("protocol.yaml", "search_plan.yaml", "extraction_schema.yaml", "synthesis_plan.yaml"):
            destination = root / name
            if not destination.exists():
                destination.write_bytes(template_root.joinpath(name).read_bytes())
        for directory in PROJECT_DIRS:
            (root / directory).mkdir(exist_ok=True)
        now = utc_now()
        metadata = {
            "schema_version": SCHEMA_VERSION, "mode": "review", "template": template,
            "created_at": now, "updated_at": now, "input_sources": [],
            "config_paths": {"protocol": "protocol.yaml", "search_plan": "search_plan.yaml", "extraction_schema": "extraction_schema.yaml", "synthesis_plan": "synthesis_plan.yaml"},
            "output_paths": {name: name for name in PROJECT_DIRS}, "run_metadata": {},
        }
        write_yaml(root / "project.yaml", metadata)
        project = cls(root=root, mode="review", metadata=metadata)
        project.validate()
        project.track_protocol(reason="project initialization")
        return project

    def validate(self) -> None:
        if self.mode == "review":
            Protocol.load(self.root / self.metadata["config_paths"]["protocol"])
            ExtractionSchema.load(self.root / self.metadata["config_paths"]["extraction_schema"])

    @property
    def protocol(self) -> Protocol:
        return Protocol.load(self.root / self.metadata["config_paths"]["protocol"])

    @property
    def extraction_schema(self) -> ExtractionSchema:
        return ExtractionSchema.load(self.root / self.metadata["config_paths"]["extraction_schema"])

    def track_protocol(self, reason: str | None = None) -> str:
        protocol = self.protocol
        state_path = self.root / ".protocol_state.yaml"
        previous = load_yaml(state_path) if state_path.exists() else {}
        old_data = previous.get("data", {})
        if previous.get("hash") != protocol.hash:
            changed = sorted(key for key in set(old_data) | set(protocol.data) if old_data.get(key) != protocol.data.get(key))
            append_jsonl(self.root / "protocol_changes.jsonl", [{
                "schema_version": SCHEMA_VERSION, "previous_hash": previous.get("hash"),
                "new_hash": protocol.hash, "timestamp": utc_now(), "changed_fields": changed,
                "reason": reason,
            }])
            write_yaml(state_path, {"hash": protocol.hash, "data": protocol.data})
        return protocol.hash
