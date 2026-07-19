import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.project import ReviewProject
from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.review_evidence import ContradictionAnalyzer, EvidenceMatrixBuilder
from review_assistant.studies import StudyExtractionStore


class EvidenceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        write_yaml(self.project.root / "protocol.yaml", protocol)
        StudyExtractionStore(self.project).ingest({"publication": {"title": "One"}, "studies": [
            {"label": "a", "fields": {"study.design": "parallel", "population.summary": "sample-a"}, "arms": [{"role": "intervention", "label": "I"}, {"role": "control", "label": "C"}], "outcomes": [{"domain": "measure", "direction": "increase", "evidence": [{"quote": "q"}]}]},
            {"label": "b", "fields": {"study.design": "crossover", "population.summary": "sample-b"}, "outcomes": [{"domain": "measure", "direction": "no_change", "evidence": []}]},
        ]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_matrix_has_deterministic_configured_columns_and_publication_id(self):
        first = EvidenceMatrixBuilder(self.project).build()
        second = EvidenceMatrixBuilder(self.project).build()
        self.assertEqual(first, second)
        payload = json.loads((self.project.root / "evidence" / "evidence_matrix.json").read_text())
        self.assertEqual(payload["columns"][0:2], ["publication_id", "study_id"])
        self.assertEqual(len({row["publication_id"] for row in first}), 1)

    def test_study_comparison_rows_are_supported(self):
        rows = EvidenceMatrixBuilder(self.project).build("study_comparison")
        self.assertIn("comparison_id", rows[0])

    def test_no_change_is_not_missing_and_forms_contradiction(self):
        groups = ContradictionAnalyzer(self.project).analyze()
        group = next(item for item in groups if item["dimensions"]["outcome.domain"] == "measure")
        self.assertIn("no_change", group["directions"])
        self.assertTrue(group["has_directional_inconsistency"])
        self.assertEqual(group["interpretation"], "candidate_explanation_only")


if __name__ == "__main__":
    unittest.main()
