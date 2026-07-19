import tempfile
import unittest
from pathlib import Path

from review_assistant.project import ReviewProject
from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.studies import StudyExtractionStore, extract_fulltext_documents, field_focus_terms, publication_id, study_id


class StudyExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stable_ids(self):
        self.assertEqual(publication_id({"doi": "10.1/X"}), publication_id({"doi": "10.1/X"}))
        self.assertNotEqual(study_id("pub", "A", 0), study_id("pub", "B", 0))

    def test_one_publication_multiple_studies_arms_and_outcomes(self):
        record = {"publication": {"title": "Publication", "doi": "10.1/x"}, "studies": [
            {"label": "cohort-a", "fields": {"study.design": "design-a", "population.summary": "sample-a"}, "arms": [{"role": "exposed", "label": "A"}], "outcomes": [{"domain": "measure-a", "direction": "increase", "evidence": [{"quote": "reported result", "page": "2"}]}]},
            {"label": "cohort-b", "fields": {"study.design": "design-b"}, "outcomes": [{"domain": "measure-b", "direction": "no_change", "evidence": []}]},
        ]}
        publication, studies, outcomes = StudyExtractionStore(self.project).ingest(record)
        self.assertEqual(len(publication.study_ids), 2)
        self.assertEqual((len(studies), len(outcomes), len(studies[0].arms)), (2, 2, 1))
        self.assertEqual(studies[1].fields["population.summary"], "not_reported")
        evidence_id = outcomes[0].evidence[0].evidence_id
        self.assertTrue(evidence_id.startswith("evidence_"))
        self.assertEqual(evidence_id, outcomes[0].evidence[0].evidence_id)

    def test_failed_quote_is_saved_as_error_not_dropped(self):
        record = {"publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [{"domain": "x", "direction": "unclear", "evidence": [{"quote": "bad"}]}]}]}
        _, _, outcomes = StudyExtractionStore(self.project, quote_validator=lambda q: False).ingest(record)
        self.assertEqual(outcomes[0].evidence[0].validation_status, "failed")
        self.assertIn("quote_verification_failed", (self.project.root / "extraction" / "extraction_errors.jsonl").read_text())

    def test_focus_terms_come_from_schema_metadata(self):
        terms = field_focus_terms({"fields": {"sample.variable": {"description": "Configured descriptor", "aliases": ["configured alias"], "extraction_instruction": "Find explicit report"}}})
        self.assertIn("Configured descriptor", terms)
        self.assertIn("configured alias", terms)

    def test_fulltext_extraction_uses_injected_schema_driven_extractor(self):
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        write_yaml(self.project.root / "protocol.yaml", protocol)
        pdf = Path(self.tmp.name) / "paper.pdf"
        pdf.write_bytes(b"placeholder")
        payload = {"publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [{"domain": "x", "direction": "unclear", "evidence": [{"quote": "reported text"}]}]}]}
        from unittest.mock import patch
        with patch("review_assistant.utils.extract_pdf_text", return_value="reported text in source"):
            result = extract_fulltext_documents(self.project, [pdf], model="mock", extractor=lambda pack, schema, path: payload)
        self.assertEqual(result, {"completed": 1, "failed": 0})
        self.assertIn("passed", (self.project.root / "extraction" / "outcomes.jsonl").read_text())

    def test_fulltext_failure_is_preserved(self):
        pdf = Path(self.tmp.name) / "paper.pdf"
        pdf.write_bytes(b"placeholder")
        from unittest.mock import patch
        with patch("review_assistant.utils.extract_pdf_text", side_effect=ValueError("broken")):
            result = extract_fulltext_documents(self.project, [pdf], model="mock", extractor=lambda *args: {})
        self.assertEqual(result["failed"], 1)
        self.assertIn("document_extraction_failed", (self.project.root / "extraction" / "extraction_errors.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
