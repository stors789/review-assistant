import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_synthesis import build_evidence_memos, fixture_writer, resolve_synthesis_plan, synthesize_review
from review_assistant.studies import StudyExtractionStore


class ReviewSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        protocol["review"]["title"] = "Configured review"
        protocol["synthesis"]["required_sections"] = ["Required evidence", "Required gap"]
        write_yaml(self.project.root / "protocol.yaml", protocol)
        write_yaml(self.project.root / "synthesis_plan.yaml", {"sections": [
            {"section_id": "S01", "title": "Required evidence", "evidence_filter": {"include_all_studies": True}},
            {"section_id": "S02", "title": "Required gap", "evidence_filter": {"no_evidence": True}},
        ], "settings": {"evidence_batch_size": 25}})
        studies = []
        for index in range(10):
            studies.append({"label": str(index), "fields": {"study.design": "configured", "population.summary": "sample"}, "outcomes": [{"domain": "configured-domain", "effect_direction": "increase" if index % 2 == 0 else "decrease", "support_relation": "supports" if index % 2 == 0 else "contradicts", "evidence": [{"quote": "reported"}]}]})
        StudyExtractionStore(self.project).ingest({"publication": {"title": "P"}, "studies": studies})

    def tearDown(self):
        self.tmp.cleanup()

    def test_required_sections_are_retained(self):
        plan = resolve_synthesis_plan(self.project)
        self.assertEqual([item["title"] for item in plan["sections"]], ["Required evidence", "Required gap"])

    def test_review_does_not_drop_to_eight_and_all_enter_memos(self):
        plan = resolve_synthesis_plan(self.project)
        memos = build_evidence_memos(self.project, plan)
        ids = {sid for memo in memos if memo["section_id"] == "S01" for sid in memo["study_ids"]}
        self.assertEqual(len(ids), 10)

    def test_unmatched_section_stays_empty_without_fallback(self):
        plan = resolve_synthesis_plan(self.project)
        gap = next(item for item in plan["sections"] if item["section_id"] == "S02")
        self.assertEqual(gap["included_study_ids"], [])
        self.assertEqual(gap["missing_evidence"], ["evidence_insufficient"])
        self.assertEqual(len(gap["excluded_study_ids"]), 10)

    def test_composable_outcome_and_study_field_filters(self):
        write_yaml(self.project.root / "synthesis_plan.yaml", {"sections": [{
            "section_id": "S01", "title": "Required evidence", "evidence_filter": {
                "outcome_domains": ["configured-domain"],
                "support_relations": ["supports"],
                "study_field_equals": {"study.design": "configured"},
            },
        }]})
        plan = resolve_synthesis_plan(self.project)
        selected = plan["sections"][0]
        self.assertEqual(len(selected["included_study_ids"]), 5)
        self.assertTrue(all(reasons == ["included_by_rule"] for sid, reasons in selected["selection_explanations"].items() if sid in selected["included_study_ids"]))

    def test_structured_writer_citations_appear_in_draft_and_claim_map(self):
        report = synthesize_review(self.project, fixture_writer)
        self.assertIn("Eligible evidence for this section", report)
        claim_map = json.loads((self.project.root / "synthesis" / "claim_map.json").read_text())
        self.assertTrue(any(claim["supporting_studies"] for claim in claim_map["claims"]))

    def test_writer_structured_claims_are_primary_claim_map_source(self):
        study_id = resolve_synthesis_plan(self.project)["sections"][0]["included_study_ids"][0]

        def writer(**kwargs):
            sentence = f"Configured evidence was observed [{study_id}]."
            return {"section_text": sentence, "claims": [{
                "sentence": sentence, "supporting_study_ids": [study_id],
                "contradicting_study_ids": [], "scope_status": "unclear",
                "population_levels": ["configured-level"], "causal_strength": "descriptive",
            }]}

        synthesize_review(self.project, writer)
        claim_map = json.loads((self.project.root / "synthesis" / "claim_map.json").read_text())
        self.assertEqual(claim_map["claims"][0]["sentence"], f"Configured evidence was observed [{study_id}].")
        self.assertEqual(claim_map["claims"][0]["protocol_scope_status"], "unclear")
        self.assertEqual(claim_map["claims"][0]["population_evidence_levels"], ["configured-level"])

    def test_writer_invalid_study_reference_is_preserved_for_audit(self):
        synthesize_review(self.project, lambda **kwargs: {
            "section_text": "Unsupported reference [study_missing].",
            "claims": [{"sentence": "Unsupported reference [study_missing].", "supporting_study_ids": ["study_missing"]}],
        })
        claim_map = json.loads((self.project.root / "synthesis" / "claim_map.json").read_text())
        self.assertEqual(claim_map["claims"][0]["invalid_supporting_studies"], ["study_missing"])

    @patch("review_assistant.llm_client.call_json")
    @patch("review_assistant.llm_client.get_client")
    def test_default_review_writer_calls_configured_llm(self, get_client, call_json):
        call_json.return_value = {"section_text": "Structured LLM section.", "claims": []}
        synthesize_review(self.project, model="configured-model")
        self.assertTrue(call_json.called)
        self.assertTrue(all(call.args[3] == "configured-model" for call in call_json.call_args_list))
        metadata = json.loads((self.project.root / "synthesis" / "synthesis_metadata.json").read_text())
        self.assertEqual(metadata["writer"], "llm")
        self.assertFalse(metadata["placeholder"])

    def test_offline_placeholder_is_visibly_marked(self):
        report = synthesize_review(self.project, offline_placeholder=True)
        self.assertIn("PLACEHOLDER SYNTHESIS", report)
        metadata = json.loads((self.project.root / "synthesis" / "synthesis_metadata.json").read_text())
        self.assertTrue(metadata["placeholder"])


if __name__ == "__main__":
    unittest.main()
