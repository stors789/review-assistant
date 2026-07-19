import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_synthesis import build_evidence_memos, resolve_synthesis_plan, synthesize_review
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

    def test_supporting_and_opposing_evidence_appear_in_draft_and_claim_map(self):
        report = synthesize_review(self.project)
        self.assertIn("supporting:", report)
        self.assertIn("opposing:", report)
        claim_map = json.loads((self.project.root / "synthesis" / "claim_map.json").read_text())
        self.assertTrue(any(claim["supporting_studies"] for claim in claim_map["claims"]))


if __name__ == "__main__":
    unittest.main()
