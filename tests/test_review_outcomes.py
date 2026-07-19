import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.studies import StudyExtractionStore, validate_quote_in_text


class ReviewOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")

    def tearDown(self):
        self.tmp.cleanup()

    def test_effect_direction_does_not_imply_support_relation(self):
        _, _, outcomes = StudyExtractionStore(self.project).ingest({
            "publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [
                {"domain": "one", "direction": "increase", "evidence": [{"quote": "reported evidence"}]},
                {"domain": "two", "direction": "decrease", "evidence": [{"quote": "reported evidence"}]},
            ]}],
        })
        self.assertEqual([item.effect_direction for item in outcomes], ["increase", "decrease"])
        self.assertEqual([item.support_relation for item in outcomes], ["unclear", "unclear"])

    def test_configured_domain_direction_derives_support_relation(self):
        schema = load_yaml(self.project.root / "extraction_schema.yaml")
        schema["outcome_domains"] = {"configured": {"beneficial_direction": "decrease"}}
        write_yaml(self.project.root / "extraction_schema.yaml", schema)
        _, _, outcomes = StudyExtractionStore(self.project).ingest({
            "publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [
                {"domain": "configured", "effect_direction": "decrease", "evidence": [{"quote": "reported evidence"}]},
            ]}],
        })
        self.assertEqual(outcomes[0].support_relation, "supports")

    def test_actual_outcome_schema_errors_preserve_raw_record(self):
        StudyExtractionStore(self.project).ingest({
            "publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [
                {"domain": "x", "effect_direction": "sideways", "support_relation": "supports", "evidence": []},
            ]}],
        })
        errors = [json.loads(line) for line in (self.project.root / "extraction" / "extraction_errors.jsonl").read_text().splitlines()]
        outcome_errors = [item for item in errors if item.get("error") == "outcome_schema_validation_failed"]
        self.assertTrue(outcome_errors)
        self.assertEqual(outcome_errors[0]["raw_record"]["effect_direction"], "sideways")

    def test_manual_quote_is_unverified_and_normalized_fulltext_can_pass(self):
        _, _, outcomes = StudyExtractionStore(self.project).ingest({
            "publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [
                {"domain": "x", "effect_direction": "unclear", "support_relation": "unclear", "evidence": [{"quote": "A meaningful source quotation."}]},
            ]}],
        })
        self.assertEqual(outcomes[0].evidence[0].validation_status, "unverified")
        result = validate_quote_in_text("The inter-\nvention changed the value.", "The intervention changed the value.")
        self.assertEqual(result["validation_status"], "passed")
        self.assertEqual(result["validation_method"], "normalized")


if __name__ == "__main__":
    unittest.main()
