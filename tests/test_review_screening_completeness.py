import tempfile
import unittest
from pathlib import Path

from review_assistant.eligibility import resolve_eligibility
from review_assistant.io_utils import append_jsonl
from review_assistant.project import ReviewProject
from review_assistant.review_audit import ReviewAuditor
from review_assistant.screening import ScreeningStore, resolve_screening_completeness


class ScreeningCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        append_jsonl(self.project.root / "search" / "deduplicated_records.jsonl", [
            {"record_id": "record_included", "title": "Included record"},
            {"record_id": "record_excluded", "title": "Excluded record"},
            {"record_id": "record_missing", "title": "Unscreened record"},
        ])
        store = ScreeningStore(self.project)
        store.decide("record_included", "title_abstract", "include", "tester")
        store.decide("record_included", "fulltext", "include", "tester")
        store.decide("record_excluded", "title_abstract", "exclude", "tester", reason_code="other")

    def tearDown(self):
        self.tmp.cleanup()

    def test_universe_not_decision_history_is_incomplete(self):
        completeness = resolve_screening_completeness(self.project)
        self.assertEqual(completeness["all_search_record_ids"], [
            "record_excluded", "record_included", "record_missing",
        ])
        self.assertEqual(completeness["title_abstract_missing_ids"], ["record_missing"])
        self.assertEqual(completeness["fulltext_required_ids"], ["record_included"])
        self.assertEqual(completeness["fulltext_missing_ids"], [])
        self.assertTrue((self.project.root / "screening" / "completeness.json").exists())
        self.assertIn("record_missing", resolve_eligibility(self.project).incomplete_record_ids)

    def test_adding_latest_decision_removes_missing_record(self):
        store = ScreeningStore(self.project)
        store.decide("record_missing", "title_abstract", "exclude", "tester", reason_code="other")
        completeness = resolve_screening_completeness(self.project)
        self.assertEqual(completeness["title_abstract_missing_ids"], [])
        self.assertEqual(completeness["title_abstract_uncertain_ids"], [])
        self.assertEqual(completeness["illegal_state_ids"], [])
        self.assertNotIn("record_missing", resolve_eligibility(self.project).incomplete_record_ids)

    def test_strict_audit_reports_search_record_absent_from_decision_history(self):
        summary = ReviewAuditor(self.project).run()
        self.assertIn("screening_required_but_incomplete", summary["counts"])
        screening_issue = next(item for item in summary["issues"] if item["check"] == "screening_required_but_incomplete")
        self.assertIn("record_missing", screening_issue["context"]["record_ids"])
        ScreeningStore(self.project).decide("record_missing", "title_abstract", "exclude", "tester", reason_code="other")
        summary = ReviewAuditor(self.project).run()
        self.assertNotIn("screening_required_but_incomplete", summary["counts"])


if __name__ == "__main__":
    unittest.main()
