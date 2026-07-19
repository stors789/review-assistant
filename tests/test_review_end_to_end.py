import csv
import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.cli import main
from review_assistant.io_utils import append_jsonl, load_yaml, read_jsonl, write_yaml


class ReviewEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "review"
        self.assertEqual(main(["review", "init", str(self.root)]), 0)
        protocol = load_yaml(self.root / "protocol.yaml")
        protocol["review"]["title"] = "Offline configured review"
        protocol["review"]["primary_question"] = "What does the configured evidence show?"
        protocol["synthesis"]["required_sections"] = ["Configured evidence", "Configured gap"]
        protocol["screening"]["enforcement"] = "required"
        write_yaml(self.root / "protocol.yaml", protocol)
        write_yaml(self.root / "search_plan.yaml", {
            "searches": [], "seed_records": [{"title": "Included publication"}, {"title": "Excluded publication"}],
        })
        write_yaml(self.root / "synthesis_plan.yaml", {"sections": [
            {"section_id": "S01", "title": "Configured evidence", "evidence_filter": {"outcome_domains": ["configured-domain"]}},
            {"section_id": "S02", "title": "Configured gap", "evidence_filter": {"outcome_domains": ["missing-domain"]}},
        ]})
        append_jsonl(self.root / "search" / "deduplicated_records.jsonl", [
            {"record_id": "record_a", "title": "Included publication", "doi": "10.1/a", "source_file": "included.txt"},
            {"record_id": "record_b", "title": "Excluded publication", "doi": "10.1/b", "source_file": "excluded.txt"},
        ])
        (self.root / "fulltext" / "included.txt").write_text("A sufficiently long verified quotation supports the configured result.", encoding="utf-8")
        (self.root / "fulltext" / "excluded.txt").write_text("A sufficiently long quotation belongs to excluded evidence.", encoding="utf-8")
        self._import_screening()
        self.included_study = self._extract(
            "record_a", "Included publication", "included.txt", "configured-domain",
            "A sufficiently long verified quotation supports the configured result.",
        )
        self.excluded_study = self._extract(
            "record_b", "Excluded publication", "excluded.txt", "excluded-domain",
            "A sufficiently long quotation belongs to excluded evidence.",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _import_screening(self):
        path = Path(self.tmp.name) / "screening.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["record_id", "stage", "decision", "reason_code"])
            writer.writeheader()
            writer.writerows([
                {"record_id": "record_a", "stage": "title_abstract", "decision": "include", "reason_code": ""},
                {"record_id": "record_b", "stage": "title_abstract", "decision": "include", "reason_code": ""},
                {"record_id": "record_a", "stage": "fulltext", "decision": "include", "reason_code": ""},
                {"record_id": "record_b", "stage": "fulltext", "decision": "exclude", "reason_code": "other"},
            ])
        mappings = ["record_id=record_id", "stage=stage", "decision=decision", "reason_code=reason_code"]
        argv = ["review", "screen", "import", "--project", str(self.root), str(path)]
        for mapping in mappings:
            argv.extend(["--map", mapping])
        self.assertEqual(main(argv), 0)

    def _extract(self, record_id, title, source_file, domain, quote):
        path = Path(self.tmp.name) / f"{record_id}.json"
        payload = {
            "source_record_id": record_id, "source_file": source_file,
            "publication": {"title": title}, "studies": [{
                "label": record_id,
                "fields": {"study.design": "configured-design", "population.summary": "configured-population"},
                "outcomes": [{
                    "domain": domain, "effect_direction": "increase", "support_relation": "supports",
                    "evidence": [{"quote": quote, "page": "1", "manual_verified": True}],
                }],
            }],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(main(["review", "extract", "--project", str(self.root), "--input", str(path)]), 0)
        links = read_jsonl(self.root / "extraction" / "study_record_links.jsonl")
        return next(item["study_id"] for item in reversed(links) if item["record_id"] == record_id)

    def test_clean_offline_cli_flow_is_closed_and_strict_passes(self):
        self.assertEqual(main(["review", "fulltext", "status", "--project", str(self.root)]), 0)
        code = main([
            "review", "run", "--project", str(self.root), "--from-stage", "matrix",
            "--offline-fixture-writer", "--strict",
        ])
        self.assertEqual(code, 0)

        matrix = json.loads((self.root / "evidence" / "evidence_matrix.json").read_text())
        plan = json.loads((self.root / "synthesis" / "resolved_synthesis_plan.json").read_text())
        claim_map = json.loads((self.root / "synthesis" / "claim_map.json").read_text())
        audit = json.loads((self.root / "audit" / "audit_summary.json").read_text())
        draft = (self.root / "synthesis" / "review_draft.md").read_text()
        self.assertEqual([row["study_id"] for row in matrix["rows"]], [self.included_study])
        self.assertEqual(plan["sections"][0]["included_study_ids"], [self.included_study])
        self.assertEqual(plan["sections"][1]["included_study_ids"], [])
        self.assertEqual({sid for claim in claim_map["claims"] for sid in claim["supporting_studies"]}, {self.included_study})
        self.assertNotIn(self.excluded_study, draft)
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["issue_count"], 0)
        run = next((self.root / "runs").iterdir())
        metadata = json.loads((run / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "completed")


if __name__ == "__main__":
    unittest.main()
