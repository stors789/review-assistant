import unittest
from pathlib import Path

from review_assistant.io_utils import load_yaml
from review_assistant.protocol import ExtractionSchema, Protocol


class ReviewExampleTests(unittest.TestCase):
    def test_all_example_protocols_and_schemas_validate(self):
        root = Path(__file__).resolve().parents[1] / "examples"
        names = sorted(path.name for path in root.iterdir() if path.is_dir())
        self.assertEqual(names, ["40hz-neurodegeneration", "generic-fictional"])
        for directory in root.iterdir():
            if directory.is_dir():
                Protocol.validate(load_yaml(directory / "protocol.yaml"))
                ExtractionSchema.validate(load_yaml(directory / "extraction_schema.yaml"))

    def test_topic_example_terms_do_not_leak_into_core_python(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = ("40 hz", "genus", "amyloid", "visual flicker")
        for path in (root / "review_assistant").glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for term in forbidden:
                self.assertNotIn(term, text, f"{term!r} leaked into {path.name}")


if __name__ == "__main__":
    unittest.main()
