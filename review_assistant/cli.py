"""Unified Explore/Review command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .bootstrap import bootstrap_from_explore
from .project import ReviewProject, discover_templates
from .review_audit import ReviewAuditor
from .review_evidence import ContradictionAnalyzer, EvidenceMatrixBuilder
from .review_search import SearchOrchestrator
from .review_synthesis import fixture_writer, synthesize_review
from .run_state import RunState
from .screening import ScreeningStore
from .studies import StudyExtractionStore, extract_fulltext_documents

STAGES = ["search", "screen", "fulltext", "extract", "matrix", "analyze", "synthesize", "audit"]


class WaitingForInput(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review-assistant")
    modes = parser.add_subparsers(dest="mode", required=True)
    explore = modes.add_parser("explore", help="Exploratory narrative synthesis")
    explore_sub = explore.add_subparsers(dest="command", required=True)
    for command in ("run", "audit"):
        child = explore_sub.add_parser(command)
        child.add_argument("args", nargs=argparse.REMAINDER)

    review = modes.add_parser("review", help="Protocol-driven formal evidence synthesis")
    commands = review.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("path", type=Path)
    init.add_argument("--template", choices=discover_templates(), default="generic-structured-review")
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--from-explore", type=Path, required=True)
    bootstrap.add_argument("--output", type=Path, required=True)
    bootstrap.add_argument("--template", choices=discover_templates(), default="generic-structured-review")
    search = commands.add_parser("search")
    _project_arg(search)
    search.add_argument("--search-id")
    search.add_argument("--limit", type=int, default=100)
    search.add_argument("--allow-empty-search", action="store_true")
    screen = commands.add_parser("screen")
    screen_sub = screen.add_subparsers(dest="screen_command", required=True)
    screen_import = screen_sub.add_parser("import")
    _project_arg(screen_import)
    screen_import.add_argument("csv", type=Path)
    screen_import.add_argument("--map", action="append", default=[], metavar="TARGET=SOURCE")
    screen_import.add_argument("--stage", choices=["title_abstract", "fulltext"])
    screen_import.add_argument("--reviewer", default="import")
    fulltext = commands.add_parser("fulltext")
    fulltext_sub = fulltext.add_subparsers(dest="fulltext_command", required=True)
    status = fulltext_sub.add_parser("status")
    _project_arg(status)
    extract = commands.add_parser("extract")
    _project_arg(extract)
    extraction_source = extract.add_mutually_exclusive_group(required=True)
    extraction_source.add_argument("--input", type=Path, help="Structured JSON extraction to validate and persist")
    extraction_source.add_argument("--fulltext-dir", type=Path, help="Extract all PDFs using the configured schema")
    extract.add_argument("--model", default="deepseek-v4-pro")
    matrix = commands.add_parser("matrix")
    matrix_sub = matrix.add_subparsers(dest="matrix_command", required=True)
    build = matrix_sub.add_parser("build")
    _project_arg(build)
    build.add_argument("--row-mode", choices=["study", "study_comparison"], default="study")
    evidence = commands.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    analyze = evidence_sub.add_parser("analyze")
    _project_arg(analyze)
    synthesize = commands.add_parser("synthesize")
    _project_arg(synthesize)
    synthesize.add_argument("--model")
    synthesis_mode = synthesize.add_mutually_exclusive_group()
    synthesis_mode.add_argument("--offline-placeholder", action="store_true")
    synthesis_mode.add_argument("--offline-fixture-writer", action="store_true", help=argparse.SUPPRESS)
    audit = commands.add_parser("audit")
    _project_arg(audit)
    audit.add_argument("--strict", action="store_true")
    run = commands.add_parser("run")
    _project_arg(run)
    run.add_argument("--from-stage", choices=STAGES)
    run.add_argument("--to-stage", choices=STAGES)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--force", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--strict", action="store_true")
    run.add_argument("--config", type=Path)
    run.add_argument("--model")
    run_mode = run.add_mutually_exclusive_group()
    run_mode.add_argument("--offline-placeholder", action="store_true")
    run_mode.add_argument("--offline-fixture-writer", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--allow-empty-search", action="store_true")
    return parser


def _project_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True)


def _mapping(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid mapping {value!r}; expected TARGET=SOURCE")
        target, source = value.split("=", 1)
        result[target] = source
    return result


def default_search_runners() -> dict[str, Callable[[str, int], list[dict[str, Any]]]]:
    from .auto_lit import _search_pubmed, _search_ss
    return {
        "pubmed": lambda query, limit: _search_pubmed(query, limit),
        "semantic_scholar": lambda query, limit: _search_ss(query, limit),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "explore":
        from . import explore_synthesize
        delegated_args = list(args.args)
        if args.command == "audit" and "--skip-step1" not in delegated_args:
            delegated_args.append("--skip-step1")
        original = sys.argv
        try:
            sys.argv = [f"review-assistant explore {args.command}", *delegated_args]
            explore_synthesize.main()
        finally:
            sys.argv = original
        return 0
    if args.command == "init":
        project = ReviewProject.initialize_review(args.path, args.template)
        print(project.root)
        return 0
    if args.command == "bootstrap":
        project = bootstrap_from_explore(args.from_explore, args.output, args.template)
        print(project.root / "bootstrap_candidates.yaml")
        return 0
    project = ReviewProject.load(args.project)
    project.validate()
    if args.command == "search":
        try:
            result = SearchOrchestrator(project, default_search_runners()).run(args.search_id, args.limit, allow_empty=args.allow_empty_search)
        except Exception as exc:
            print(f"Search execution failed: {exc}", file=sys.stderr)
            return 1
        return 1 if result.failures else 0
    if args.command == "screen":
        count = ScreeningStore(project).import_csv(args.csv, _mapping(args.map), default_stage=args.stage, default_reviewer=args.reviewer)
        print(f"Imported {count} decisions")
        return 0
    if args.command == "fulltext":
        result = fulltext_status(project)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["missing_record_ids"] else 0
    if args.command == "extract":
        if args.input:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            StudyExtractionStore(project).ingest(payload)
            return 0
        pdfs = sorted(args.fulltext_dir.glob("*.pdf"))
        if not pdfs:
            raise SystemExit(f"No PDFs found in {args.fulltext_dir}")
        result = extract_fulltext_documents(project, pdfs, model=args.model)
        print(json.dumps(result))
        return 1 if result["failed"] else 0
    if args.command == "matrix":
        EvidenceMatrixBuilder(project).build(args.row_mode)
        return 0
    if args.command == "evidence":
        ContradictionAnalyzer(project).analyze()
        return 0
    if args.command == "synthesize":
        injected = fixture_writer if args.offline_fixture_writer else None
        synthesize_review(project, injected, model=args.model, offline_placeholder=args.offline_placeholder)
        return 0
    if args.command == "audit":
        try:
            summary = ReviewAuditor(project).run()
        except Exception as exc:
            print(f"Audit execution failed: {exc}", file=sys.stderr)
            return 1
        return 2 if args.strict and summary["status"] == "failed" else 0
    if args.command == "run":
        return run_pipeline(project, args)
    return 0


def run_pipeline(project: ReviewProject, args: argparse.Namespace) -> int:
    start = STAGES.index(args.from_stage) if args.from_stage else 0
    end = STAGES.index(args.to_stage) + 1 if args.to_stage else len(STAGES)
    stages = STAGES[start:end]
    if args.dry_run:
        print(json.dumps({"project": str(project.root), "stages": stages, "protocol_hash": project.protocol.hash}, indent=2))
        return 0
    if args.config and not args.config.exists():
        print(f"Run config does not exist: {args.config}", file=sys.stderr)
        return 1
    try:
        state = RunState.resume_latest(project) if args.resume else RunState.start(project)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    actions = {
        "search": lambda: (SearchOrchestrator(project, default_search_runners()).run(allow_empty=args.allow_empty_search), ["search/search_log.jsonl", "search/deduplicated_records.jsonl"]),
        "screen": lambda: (_require_screening(project), ["screening/decision_history.jsonl", "screening/prisma_counts.json"]),
        "fulltext": lambda: (_require_fulltext(project), ["fulltext", "extraction/record_publication_links.jsonl"]),
        "extract": lambda: (_require(project.root / "extraction" / "studies.jsonl", "Run `review-assistant review extract --input ...` after full-text screening"), ["extraction/publications.jsonl", "extraction/studies.jsonl", "extraction/outcomes.jsonl"]),
        "matrix": lambda: (EvidenceMatrixBuilder(project).build(), ["evidence/evidence_matrix.csv", "evidence/evidence_matrix.json"]),
        "analyze": lambda: (ContradictionAnalyzer(project).analyze(), ["evidence/contradiction_groups.json", "evidence/heterogeneity_report.md"]),
        "synthesize": lambda: (synthesize_review(project, fixture_writer if args.offline_fixture_writer else None, model=args.model, offline_placeholder=args.offline_placeholder), ["synthesis/review_draft.md", "synthesis/claim_map.json", "synthesis/synthesis_metadata.json"]),
        "audit": lambda: (ReviewAuditor(project).run(), ["audit/audit_summary.json"]),
    }
    try:
        for stage in stages:
            if args.resume and not args.force and state.stages.get(stage, {}).get("status") == "completed":
                continue
            state.begin_stage(stage)
            _, outputs = actions[stage]()
            state.finish_stage(stage, outputs)
        state.finish()
    except WaitingForInput as exc:
        state.wait_stage(stage, str(exc))
        print(f"Stage {stage} is waiting for input: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        state.fail_stage(stage, exc)
        print(f"Stage {stage} failed: {exc}", file=sys.stderr)
        return 1
    if args.strict and "audit" in stages:
        summary = json.loads((project.root / "audit" / "audit_summary.json").read_text())
        return 2 if summary["status"] == "failed" else 0
    return 0


def _require(path: Path, instruction: str) -> None:
    if not path.exists():
        raise RuntimeError(f"Required artifact missing: {path}. {instruction}")


def _require_screening(project: ReviewProject) -> None:
    path = project.root / "screening" / "decision_history.jsonl"
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise WaitingForInput("Import screening decisions with `review-assistant review screen import`")


def fulltext_status(project: ReviewProject) -> dict[str, Any]:
    from .eligibility import latest_screening_decisions
    from .io_utils import read_jsonl
    decisions = latest_screening_decisions(project)
    included = {rid for (rid, stage), item in decisions.items() if stage == "fulltext" and item.get("decision") == "include"}
    links = read_jsonl(project.root / "extraction" / "record_publication_links.jsonl")
    structured = {str(item.get("record_id")) for item in links if item.get("record_id")}
    records = {str(item.get("record_id")): item for item in read_jsonl(project.root / "search" / "deduplicated_records.jsonl")}
    available = set(structured)
    files = []
    for path in (project.root / "fulltext").glob("*"):
        if path.is_file():
            files.append(path.name)
    for rid in included:
        record = records.get(rid, {})
        configured = record.get("source_file") or record.get("full_text_file") or record.get("file")
        if configured:
            candidate = Path(str(configured))
            if candidate.exists() or (project.root / "fulltext" / candidate.name).exists():
                available.add(rid)
    return {"included_record_ids": sorted(included), "available_record_ids": sorted(included & available), "missing_record_ids": sorted(included - available), "files": sorted(files)}


def _require_fulltext(project: ReviewProject) -> None:
    result = fulltext_status(project)
    if result["missing_record_ids"]:
        raise WaitingForInput(f"Provide and bind full text for records: {', '.join(result['missing_record_ids'])}")


if __name__ == "__main__":
    raise SystemExit(main())
