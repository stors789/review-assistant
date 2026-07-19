import tempfile
import unittest
from pathlib import Path

from review_assistant.eligibility import resolve_fulltext_status
from review_assistant.io_utils import append_jsonl, load_yaml, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.screening import ScreeningStore
from review_assistant.studies import StudyExtractionStore


class FulltextStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        append_jsonl(self.project.root / "search" / "deduplicated_records.jsonl", [{
            "record_id": "record_a", "title": "Included record", "source_file": "record_a.txt",
        }])
        store = ScreeningStore(self.project)
        store.decide("record_a", "title_abstract", "include", "tester")
        store.decide("record_a", "fulltext", "include", "tester")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_link_alone_is_not_fulltext(self):
        append_jsonl(self.project.root / "extraction" / "record_publication_links.jsonl", [{
            "record_id": "record_a", "publication_id": "publication_a",
        }])
        status = resolve_fulltext_status(self.project)
        self.assertEqual(status["fulltext_available_record_ids"], [])
        self.assertEqual(status["structured_extraction_available_record_ids"], [])
        self.assertEqual(status["fulltext_missing_record_ids"], ["record_a"])

    def test_structured_and_fulltext_availability_are_separate(self):
        StudyExtractionStore(self.project).ingest({
            "source_record_id": "record_a", "publication": {"title": "Included record"},
            "studies": [{"label": "study", "fields": {}, "outcomes": []}],
        })
        status = resolve_fulltext_status(self.project)
        self.assertEqual(status["structured_extraction_available_record_ids"], ["record_a"])
        self.assertEqual(status["fulltext_available_record_ids"], [])
        self.assertEqual(status["both_available_record_ids"], [])
        (self.project.root / "fulltext" / "record_a.txt").write_text("full text", encoding="utf-8")
        status = resolve_fulltext_status(self.project)
        self.assertEqual(status["fulltext_available_record_ids"], ["record_a"])
        self.assertEqual(status["both_available_record_ids"], ["record_a"])

    def test_structured_extraction_allowed_changes_blocking_requirement(self):
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["fulltext"] = {"requirement": "structured_extraction_allowed"}
        write_yaml(self.project.root / "protocol.yaml", protocol)
        StudyExtractionStore(self.project).ingest({
            "source_record_id": "record_a", "publication": {"title": "Included record"},
            "studies": [{"label": "study", "fields": {}, "outcomes": []}],
        })
        status = resolve_fulltext_status(self.project)
        self.assertEqual(status["requirement"], "structured_extraction_allowed")
        self.assertEqual(status["blocking_record_ids"], [])
        self.assertEqual(status["fulltext_missing_record_ids"], ["record_a"])


if __name__ == "__main__":
    unittest.main()
