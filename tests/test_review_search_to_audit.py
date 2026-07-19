import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_assistant.cli import main
from review_assistant.io_utils import load_yaml, read_jsonl, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.studies import current_outcomes


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
        strict_v1 = main(["review", "audit", "--project", str(self.root), "--strict"])
        self.assertEqual(strict_v1, 0)

        plan = json.loads((self.root / "synthesis" / "resolved_synthesis_plan.json").read_text())
        matrix = json.loads((self.root / "evidence" / "evidence_matrix.json").read_text())
        claims = json.loads((self.root / "synthesis" / "claim_map.json").read_text())["claims"]
        claims_v1_count = len(claims)
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

        initial_active = current_outcomes(ReviewProject.load(self.root))
        initial_ids = {item["outcome_id"] for item in initial_active}
        initial_target_evidence = next(
            location["evidence_id"]
            for item in initial_active if item["outcome_id"] == "outcome-target"
            for location in item["evidence"]
        )

        revised = json.loads(extraction.read_text())
        revised["studies"][0]["outcomes"] = [
            revised["studies"][0]["outcomes"][1],
            {**revised["studies"][0]["outcomes"][0], "evidence": [{
                "quote": "A sufficiently long revised target quotation.", "page": "3", "manual_verified": True,
            }]},
        ]
        extraction.write_text(json.dumps(revised), encoding="utf-8")
        self.assertEqual(main(["review", "extract", "--project", str(self.root), "--input", str(extraction)]), 0)
        self.assertEqual(main(["review", "matrix", "build", "--project", str(self.root)]), 0)
        self.assertEqual(main(["review", "evidence", "analyze", "--project", str(self.root)]), 0)
        with patch("review_assistant.cli.fixture_writer") as writer:
            from review_assistant.review_synthesis import fixture_writer as real_fixture_writer
            writer.side_effect = real_fixture_writer
            self.assertEqual(main(["review", "synthesize", "--project", str(self.root), "--offline-fixture-writer"]), 0)
        strict_v2 = main(["review", "audit", "--project", str(self.root), "--strict"])
        self.assertEqual(strict_v2, 0)

        all_outcomes = read_jsonl(self.root / "extraction" / "outcomes.jsonl")
        current_outcome_rows = current_outcomes(ReviewProject.load(self.root))
        reordered_ids = {item["outcome_id"] for item in current_outcome_rows}
        self.assertEqual(reordered_ids, initial_ids)
        self.assertEqual([item["outcome_id"] for item in current_outcome_rows], ["outcome-other", "outcome-target"])
        revised_target_evidence = next(
            location["evidence_id"]
            for item in current_outcome_rows if item["outcome_id"] == "outcome-target"
            for location in item["evidence"]
        )
        self.assertNotEqual(revised_target_evidence, initial_target_evidence)
        self.assertTrue(any(
            item.get("outcome_id") == "outcome-target" and item.get("status") == "superseded"
            for item in all_outcomes
        ))
        memo_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.root / "evidence" / "evidence_memos").glob("*.md"))
        self.assertNotIn("A sufficiently long verified target quotation.", memo_text)
        self.assertIn("A sufficiently long revised target quotation.", memo_text)

        claims_v2 = json.loads((self.root / "synthesis" / "claim_map.json").read_text())["claims"]
        validation_v2 = json.loads((self.root / "synthesis" / "section_validation.json").read_text())
        audit_v2 = json.loads((self.root / "audit" / "audit_summary.json").read_text())

        self.assertEqual(main([
            "review", "run", "--project", str(self.root), "--from-stage", "matrix", "--to-stage", "audit",
            "--offline-fixture-writer", "--strict",
        ]), 0)
        run_roots = sorted((self.root / "runs").iterdir())
        metadata = json.loads((run_roots[-1] / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "completed")

        if os.environ.get("REVIEW_BOUNDARY_REPORT") == "1":
            complete_claims = [claim for claim in claims_v2 if claim.get("linkage_status") == "complete"]
            complete_studies = sorted({
                study_id
                for claim in complete_claims
                for study_id in claim.get("supporting_studies", []) + claim.get("contradicting_studies", [])
            })
            complete_outcomes = sorted({
                outcome_id
                for claim in complete_claims
                for outcome_id in claim.get("supporting_outcomes", []) + claim.get("contradicting_outcomes", [])
            })
            unknown_tokens = sorted({
                token
                for section in validation_v2.get("sections", [])
                for token in section.get("unknown_citation_tokens", [])
            })
            filter_violation_keys = {
                "supporting_outcome_outside_section_filter",
                "contradicting_outcome_outside_section_filter",
                "outcome_domain_outside_section",
                "effect_direction_outside_section",
                "support_relation_outside_section",
            }
            filter_violations = sum(
                count for key, count in audit_v2.get("counts", {}).items() if key in filter_violation_keys
            )
            print("REVIEW_BOUNDARY_REPORT " + json.dumps({
                "initial_current_outcome_ids": [item["outcome_id"] for item in initial_active],
                "current_outcome_ids_after_reorder": [item["outcome_id"] for item in current_outcome_rows],
                "outcome_ids_preserved": initial_ids == reordered_ids,
                "active_outcomes": len(current_outcome_rows),
                "superseded_outcomes": sum(item.get("status") == "superseded" for item in all_outcomes),
                "claims_v1": claims_v1_count,
                "claims_v2": len(claims_v2),
                "studies_with_complete_linkage": len(complete_studies),
                "outcomes_with_complete_linkage": len(complete_outcomes),
                "unknown_citation_tokens": unknown_tokens,
                "section_filter_violations": filter_violations,
                "audit_v1": {"status": audit["status"], "issue_count": audit["issue_count"]},
                "audit_v2": {"status": audit_v2["status"], "issue_count": audit_v2["issue_count"]},
                "strict_v1": strict_v1,
                "strict_v2": strict_v2,
            }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
