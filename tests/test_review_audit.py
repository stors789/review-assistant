import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml, write_json, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_audit import ReviewAuditor
from review_assistant.review_evidence import ContradictionAnalyzer
from review_assistant.review_synthesis import fixture_writer, synthesize_review
from review_assistant.studies import StudyExtractionStore


class ReviewAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        protocol["synthesis"]["required_sections"] = ["Evidence"]
        write_yaml(self.project.root / "protocol.yaml", protocol)
        write_yaml(self.project.root / "synthesis_plan.yaml", {"sections": [{
            "section_id": "S01", "title": "Evidence", "evidence_filter": {"include_all_studies": True},
        }]})
        StudyExtractionStore(self.project).ingest({"publication": {"title": "P"}, "studies": [
            {"label": "a", "fields": {}, "outcomes": [{"domain": "x", "effect_direction": "increase", "support_relation": "supports", "evidence": [{"quote": "q"}]}]},
            {"label": "b", "fields": {}, "outcomes": [{"domain": "x", "effect_direction": "no_change", "support_relation": "contradicts", "evidence": [{"quote": "q"}]}]},
        ]})
        ContradictionAnalyzer(self.project).analyze()
        synthesize_review(self.project, fixture_writer)

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

    def test_unverified_quote_supporting_claim_is_strict_issue(self):
        summary = ReviewAuditor(self.project).run()
        self.assertIn("unverified_critical_quote", summary["counts"])

    def test_placeholder_and_empty_search_plan_are_strict_issues(self):
        synthesize_review(self.project, offline_placeholder=True)
        summary = ReviewAuditor(self.project).run()
        self.assertIn("placeholder_synthesis_used", summary["counts"])
        self.assertIn("empty_search_plan", summary["counts"])

    def test_ineligible_study_citation_is_not_reported_as_merely_unresolved(self):
        path = self.project.root / "synthesis" / "claim_map.json"
        payload = json.loads(path.read_text())
        known = payload["claims"][0]["supporting_studies"][0]
        # Change the eligibility gate after synthesis so this real study becomes ineligible.
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "required"
        write_yaml(self.project.root / "protocol.yaml", protocol)
        payload["claims"][0]["supporting_studies"] = [known]
        write_json(path, payload)
        summary = ReviewAuditor(self.project).run()
        self.assertIn("citation_to_excluded_study", summary["counts"])
        self.assertIn("included_ineligible_evidence", summary["counts"])

    def test_writer_population_and_causal_flags_fail_audit(self):
        study_id = json.loads((self.project.root / "synthesis" / "claim_map.json").read_text())["claims"][0]["supporting_studies"][0]
        synthesize_review(self.project, lambda **kwargs: {
            "section_text": f"Overstated configured claim [{study_id}].",
            "claims": [{
                "sentence": f"Overstated configured claim [{study_id}].",
                "supporting_study_ids": [study_id], "animal_to_human": True,
                "population_overgeneralization": True, "causal_inflation": True,
            }],
        })
        summary = ReviewAuditor(self.project).run()
        self.assertIn("animal_to_human_extrapolation", summary["counts"])
        self.assertIn("population_level_overgeneralization", summary["counts"])
        self.assertIn("correlation_to_causation_inflation", summary["counts"])

    def test_writer_unknown_study_reference_fails_audit(self):
        synthesize_review(self.project, lambda **kwargs: {
            "section_text": "Unknown evidence [study_unknown].",
            "claims": [{"sentence": "Unknown evidence [study_unknown].", "supporting_study_ids": ["study_unknown"]}],
        })
        summary = ReviewAuditor(self.project).run()
        self.assertIn("citation_key_resolution_failure", summary["counts"])

    def test_protocol_change_after_synthesis_fails_audit(self):
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["review"]["primary_question"] = "Changed configured question"
        write_yaml(self.project.root / "protocol.yaml", protocol)
        summary = ReviewAuditor(self.project).run()
        self.assertIn("protocol_hash_mismatch", summary["counts"])

    def test_actual_outcome_schema_error_fails_audit(self):
        StudyExtractionStore(self.project).ingest({
            "publication": {"title": "Invalid outcome"}, "studies": [{"fields": {}, "outcomes": [{
                "domain": "x", "effect_direction": "invalid", "support_relation": "supports", "evidence": [],
            }]}],
        })
        summary = ReviewAuditor(self.project).run()
        self.assertIn("schema_validation_error", summary["counts"])


if __name__ == "__main__":
    unittest.main()
