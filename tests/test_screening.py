import csv
import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.project import ReviewProject
from review_assistant.screening import ScreeningStore


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        self.store = ScreeningStore(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_stages_uncertain_and_history(self):
        self.store.decide("R1", "title_abstract", "uncertain", "human", ai_recommendation="exclude", ai_confidence="0.6")
        self.store.decide("R1", "title_abstract", "include", "human")
        self.store.decide("R1", "fulltext", "include", "human")
        self.assertEqual(len(self.store.history(record_id="R1")), 3)
        self.assertEqual(self.store.current("title_abstract")[0]["decision"], "include")
        self.assertEqual(self.store.history(stage="title_abstract")[-1]["previous_decision"], "uncertain")

    def test_ai_never_overrides_human_decision(self):
        self.store.decide("R1", "title_abstract", "include", "human", ai_recommendation="exclude", ai_confidence="0.99")
        self.assertEqual(self.store.current()[0]["decision"], "include")

    def test_reason_codes_are_protocol_driven(self):
        with self.assertRaises(ValueError):
            self.store.decide("R1", "fulltext", "exclude", "human", reason_code="invented")
        item = self.store.decide("R1", "fulltext", "duplicate", "human", reason_code="duplicate")
        self.assertEqual(item.decision, "duplicate")

    def test_csv_mapping_import_and_prisma_counts(self):
        path = Path(self.tmp.name) / "input.csv"
        path.write_text("id,status,why\nR1,include,\nR2,exclude,wrong_outcome\n", encoding="utf-8")
        count = self.store.import_csv(path, {"record_id": "id", "decision": "status", "reason_code": "why"})
        self.assertEqual(count, 2)
        prisma = json.loads((self.project.root / "screening" / "prisma_counts.json").read_text())
        self.assertEqual(prisma["stages"]["title_abstract"]["exclude"], 1)

    def test_bad_csv_mapping_fails_explicitly(self):
        path = Path(self.tmp.name) / "input.csv"
        path.write_text("id,status\nR1,include\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CSV columns not found"):
            self.store.import_csv(path, {"record_id": "missing", "decision": "status"})


if __name__ == "__main__":
    unittest.main()
