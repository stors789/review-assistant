import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_synthesis import (
    synthesize_review,
    validate_structured_section_output,
)
from review_assistant.studies import StudyExtractionStore


class SectionValidationTests(unittest.TestCase):
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
        StudyExtractionStore(self.project).ingest({
            "publication": {"title": "Configured publication"},
            "studies": [{"label": "a", "fields": {}, "outcomes": []}],
        })
        from review_assistant.review_synthesis import resolve_synthesis_plan
        self.section = resolve_synthesis_plan(self.project)["sections"][0]
        self.study_id = self.section["included_study_ids"][0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_output_maps_sentence_and_citation(self):
        sentence = f"The configured result was reported [{self.study_id}]."
        result = validate_structured_section_output(
            self.section,
            {"section_text": sentence, "claims": [{"sentence": sentence, "supporting_study_ids": [self.study_id]}]},
            [self.study_id],
        )
        self.assertEqual(result["coverage_status"], "passed")
        self.assertEqual(result["mapped_citation_count"], 1)
        self.assertEqual(result["unmapped_sentences"], [])

    def test_nonempty_section_with_empty_claims_fails_synthesis(self):
        with self.assertRaisesRegex(ValueError, "coverage failed"):
            synthesize_review(self.project, lambda **kwargs: {
                "section_text": f"The configured result was reported [{self.study_id}].",
                "claims": [],
            })
        validation = (self.project.root / "synthesis" / "section_validation.json").read_text()
        self.assertIn("nonempty_section_zero_claims", validation)

    def test_unmapped_sentence_and_outside_citation_are_reported(self):
        sentence = f"The configured result was reported [{self.study_id}]."
        result = validate_structured_section_output(
            self.section,
            {"section_text": sentence + " An unmapped substantive statement.", "claims": [{
                "sentence": sentence, "supporting_study_ids": [self.study_id],
            }]},
            [self.study_id],
        )
        self.assertEqual(result["coverage_status"], "failed")
        self.assertIn("substantive_sentence_not_mapped", result["errors"])

        outside = validate_structured_section_output(
            self.section,
            {"section_text": "An unsupported citation [study_outside].", "claims": [{
                "sentence": "An unsupported citation [study_outside].", "supporting_study_ids": ["study_outside"],
            }]},
            [self.study_id],
        )
        self.assertEqual(outside["coverage_status"], "failed")
        self.assertTrue(any("study_outside" in error for error in outside["errors"]))


if __name__ == "__main__":
    unittest.main()
