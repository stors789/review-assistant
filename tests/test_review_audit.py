import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml, write_json, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_audit import ReviewAuditor
from review_assistant.review_evidence import ContradictionAnalyzer
from review_assistant.review_synthesis import synthesize_review
from review_assistant.studies import StudyExtractionStore


class ReviewAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        protocol["synthesis"]["required_sections"] = ["Evidence"]
        write_yaml(self.project.root / "protocol.yaml", protocol)
        StudyExtractionStore(self.project).ingest({"publication": {"title": "P"}, "studies": [
            {"label": "a", "fields": {}, "outcomes": [{"domain": "x", "direction": "increase", "evidence": [{"quote": "q"}]}]},
            {"label": "b", "fields": {}, "outcomes": [{"domain": "x", "direction": "no_change", "evidence": [{"quote": "q"}]}]},
        ]})
        ContradictionAnalyzer(self.project).analyze()
        synthesize_review(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_generated_outputs_have_required_audit_files(self):
        summary = ReviewAuditor(self.project).run()
        self.assertTrue((self.project.root / "audit" / "audit_summary.json").exists())
        self.assertTrue((self.project.root / "audit" / "citation_audit.md").exists())
        self.assertNotIn("missing_citation", summary["counts"])
        self.assertNotIn("contradiction_omission", summary["counts"])

    def test_unsupported_scope_duplicate_extrapolation_and_unresolved(self):
        path = self.project.root / "synthesis" / "claim_map.json"
        payload = json.loads(path.read_text())
        payload["claims"].append({
            "claim_id": "C-test", "support_level": "unsupported", "supporting_studies": ["missing", "missing"],
            "contradicting_studies": [], "protocol_scope_status": "outside", "animal_to_human": True,
            "causal_inflation": True, "population_overgeneralization": True,
        })
        write_json(path, payload)
        summary = ReviewAuditor(self.project).run()
        for check in ("unsupported_claim", "scope_violation", "duplicate_study_counting", "animal_to_human_extrapolation", "correlation_to_causation_inflation", "citation_key_resolution_failure"):
            self.assertIn(check, summary["counts"])

    def test_protocol_hash_mismatch_and_missing_section(self):
        claim_path = self.project.root / "synthesis" / "claim_map.json"
        payload = json.loads(claim_path.read_text())
        payload["protocol_hash"] = "old"
        write_json(claim_path, payload)
        (self.project.root / "synthesis" / "review_draft.md").write_text("# Draft\n", encoding="utf-8")
        summary = ReviewAuditor(self.project).run()
        self.assertIn("protocol_hash_mismatch", summary["counts"])
        self.assertIn("missing_required_section", summary["counts"])

    def test_invalid_quote_is_reported(self):
        StudyExtractionStore(self.project, quote_validator=lambda q: False).ingest({"publication": {"title": "Bad"}, "studies": [{"fields": {}, "outcomes": [{"domain": "y", "evidence": [{"quote": "bad"}]}]}]})
        summary = ReviewAuditor(self.project).run()
        self.assertIn("invalid_quote", summary["counts"])


if __name__ == "__main__":
    unittest.main()
