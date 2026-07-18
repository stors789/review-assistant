import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml, write_yaml
from review_assistant.project import ReviewProject, discover_templates
from review_assistant.protocol import ConfigurationError, ExtractionSchema, Protocol


class ReviewProjectTests(unittest.TestCase):
    def test_templates_are_discovered_from_resources(self):
        self.assertEqual(discover_templates(), ["animal-intervention", "biomedical-intervention", "generic-structured-review"])

    def test_init_creates_valid_complete_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = ReviewProject.initialize_review(Path(tmp) / "review")
            self.assertEqual(project.mode, "review")
            self.assertTrue((project.root / "evidence").is_dir())
            self.assertTrue((project.root / "protocol_changes.jsonl").exists())
            project.validate()

    def test_protocol_rejects_invalid_list(self):
        data = {"review": {"title": "", "type": "generic", "primary_question": ""}, "scope": {}, "eligibility": {"inclusion_criteria": "bad"}, "synthesis": {}}
        with self.assertRaises(ConfigurationError):
            Protocol.validate(data)

    def test_schema_missing_values_and_hash_are_deterministic(self):
        schema = ExtractionSchema.validate({"fields": {"sample": {"type": "string", "required": True, "missing_value": "not_reported"}}})
        self.assertEqual(schema.apply_missing_values({}), {"sample": "not_reported"})
        self.assertEqual(schema.hash, ExtractionSchema.validate({"fields": {"sample": {"missing_value": "not_reported", "required": True, "type": "string"}}}).hash)

    def test_schema_validates_nested_enum_and_numeric_rules(self):
        schema = ExtractionSchema.validate({"fields": {
            "level": {"type": "number", "validation_rule": {"min": 0}},
            "outcomes": {"type": "list", "item_schema": {"direction": {"type": "enum", "values": ["up", "down"], "required": True}}},
        }})
        errors = schema.validate_values({"level": -1, "outcomes": [{"direction": "sideways"}, {}]})
        self.assertEqual({item["error"] for item in errors}, {"below_minimum", "expected_enum", "required"})

    def test_protocol_change_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = ReviewProject.initialize_review(Path(tmp) / "review")
            path = project.root / "protocol.yaml"
            data = load_yaml(path)
            data["review"]["title"] = "Changed"
            write_yaml(path, data)
            project.track_protocol(reason="amendment")
            lines = (project.root / "protocol_changes.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
