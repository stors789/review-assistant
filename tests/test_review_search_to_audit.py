import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_assistant.cli import main
from review_assistant.io_utils import load_yaml, read_jsonl, write_yaml


class SearchToAuditEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "review"
        self.assertEqual(main(["review", "init", str(self.root)]), 0)
        protocol = load_yaml(self.root / "protocol.yaml")
        protocol["review"]["title"] = "Offline configured review"
        protocol["review"]["primary_question"] = "What does the configured evidence show?"
        protocol["screening"]["enforcement"] = "required"
        protocol["synthesis"]["required_sections"] = ["Target evidence", "Configured gap"]
        write_yaml(self.root / "protocol.yaml", protocol)
        write_yaml(self.root / "search_plan.yaml", {
            "searches": [], "seed_records": [
                {"title": "Included record", "source_file": "record_a.txt"},
                {"title": "Excluded record", "source_file": "record_b.txt"},
                {"title": "Title abstract excluded record", "source_file": "record_c.txt"},
            ],
        })
        write_yaml(self.root / "synthesis_plan.yaml", {"sections": [
            {"section_id": "S01", "title": "Target evidence", "evidence_filter": {"outcome_domains": ["target-domain"]}},
            {"section_id": "S02", "title": "Configured gap", "evidence_filter": {"outcome_domains": ["missing-domain"]}},
        ]})

    def tearDown(self):
        self.tmp.cleanup()

    def _record_ids(self):
        return {item["title"]: item["record_id"] for item in read_jsonl(self.root / "search" / "deduplicated_records.jsonl")}

    def test_clean_cli_flow_starts_with_search_and_strict_is_exactly_zero(self):
        self.assertEqual(main(["review", "search", "--project", str(self.root)]), 0)
        records = self._record_ids()
        self.assertEqual(len(records), 3)

        screening_csv = Path(self.tmp.name) / "screening.csv"
        with screening_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["record_id", "stage", "decision", "reason_code"])
            writer.writeheader()
            writer.writerows([
                {"record_id": records["Included record"], "stage": "title_abstract", "decision": "include", "reason_code": ""},
                {"record_id": records["Included record"], "stage": "fulltext", "decision": "include", "reason_code": ""},
                {"record_id": records["Excluded record"], "stage": "title_abstract", "decision": "include", "reason_code": ""},
                {"record_id": records["Excluded record"], "stage": "fulltext", "decision": "exclude", "reason_code": "other"},
                {"record_id": records["Title abstract excluded record"], "stage": "title_abstract", "decision": "exclude", "reason_code": "other"},
            ])
        args = ["review", "screen", "import", "--project", str(self.root), str(screening_csv)]
        for mapping in ("record_id=record_id", "stage=stage", "decision=decision", "reason_code=reason_code"):
            args.extend(["--map", mapping])
        self.assertEqual(main(args), 0)

        (self.root / "fulltext" / "record_a.txt").write_text("Configured full text", encoding="utf-8")
        status_code = main(["review", "fulltext", "status", "--project", str(self.root)])
        self.assertEqual(status_code, 0)

        extraction = Path(self.tmp.name) / "record_a.json"
        extraction.write_text(json.dumps({
            "source_record_id": records["Included record"], "source_file": "record_a.txt",
            "publication": {"title": "Included record"},
            "studies": [{"label": "study-a", "fields": {
                "study.design": "configured design", "population.summary": "configured population",
            }, "outcomes": [
                {"outcome_id": "outcome-target", "domain": "target-domain", "effect_direction": "increase", "support_relation": "supports", "evidence": [{"quote": "A sufficiently long verified target quotation.", "page": "1", "manual_verified": True}]},
                {"outcome_id": "outcome-other", "domain": "other-domain", "effect_direction": "decrease", "support_relation": "contradicts", "evidence": [{"quote": "A sufficiently long verified other quotation.", "page": "2", "manual_verified": True}]},
            ]}],
        }), encoding="utf-8")
        self.assertEqual(main(["review", "extract", "--project", str(self.root), "--input", str(extraction)]), 0)

        status = json.loads((self.root / "fulltext" / "status.json").read_text())
        self.assertEqual(status["fulltext_available_record_ids"], [records["Included record"]])
        self.assertEqual(status["structured_extraction_available_record_ids"], [records["Included record"]])
        self.assertEqual(status["both_available_record_ids"], [records["Included record"]])
        self.assertEqual(status["fulltext_missing_record_ids"], [])

        self.assertEqual(main(["review", "matrix", "build", "--project", str(self.root)]), 0)
        self.assertEqual(main(["review", "evidence", "analyze", "--project", str(self.root)]), 0)
        with patch("review_assistant.cli.fixture_writer") as writer:
            from review_assistant.review_synthesis import fixture_writer as real_fixture_writer
            writer.side_effect = real_fixture_writer
            self.assertEqual(main(["review", "synthesize", "--project", str(self.root), "--offline-fixture-writer"]), 0)
            self.assertEqual(writer.call_count, 1)
            self.assertEqual(writer.call_args.kwargs["section_spec"]["section_id"], "S01")
        self.assertEqual(main(["review", "audit", "--project", str(self.root), "--strict"]), 0)

        plan = json.loads((self.root / "synthesis" / "resolved_synthesis_plan.json").read_text())
        matrix = json.loads((self.root / "evidence" / "evidence_matrix.json").read_text())
        claims = json.loads((self.root / "synthesis" / "claim_map.json").read_text())["claims"]
        validation = json.loads((self.root / "synthesis" / "section_validation.json").read_text())
        audit = json.loads((self.root / "audit" / "audit_summary.json").read_text())
        self.assertEqual(plan["sections"][0]["included_study_ids"].__len__(), 1)
        self.assertEqual(plan["sections"][1]["included_study_ids"], [])
        self.assertEqual(len(matrix["rows"]), 1)
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(claims[0]["supporting_outcomes"]), 1)
        self.assertEqual(len(claims[0]["supporting_evidence"]), 1)
        self.assertEqual(validation["sections"][1]["writer_called"], False)
        self.assertEqual(validation["sections"][1]["evidence_status"], "insufficient")
        self.assertEqual(validation["sections"][1]["unmapped_sentences"], [])
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["issue_count"], 0)

        self.assertEqual(main([
            "review", "run", "--project", str(self.root), "--from-stage", "matrix", "--to-stage", "audit",
            "--offline-fixture-writer", "--strict",
        ]), 0)
        run_roots = sorted((self.root / "runs").iterdir())
        metadata = json.loads((run_roots[-1] / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "completed")


if __name__ == "__main__":
    unittest.main()
