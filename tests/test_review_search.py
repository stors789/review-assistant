import json
import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_search import SearchOrchestrator


class ReviewSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")

    def tearDown(self):
        self.tmp.cleanup()

    def test_multiple_searches_merge_deduplicate_and_preserve_provenance(self):
        write_yaml(self.project.root / "search_plan.yaml", {"searches": [
            {"id": "one", "source": "first", "query": "query one", "enabled": True},
            {"id": "two", "source": "second", "query": "query two", "enabled": True},
        ]})
        runners = {
            "first": lambda query, limit: [{"title": "Same record", "doi": "10.1/example"}],
            "second": lambda query, limit: [{"title": "Same record", "externalIds": {"DOI": "https://doi.org/10.1/example"}}, {"title": "Unique"}],
        }
        result = SearchOrchestrator(self.project, runners, "test").run()
        self.assertEqual(len(result.records), 2)
        same = next(item for item in result.records if item["title"] == "Same record")
        self.assertEqual({p["search_id"] for p in same["search_provenance"]}, {"one", "two"})
        self.assertTrue((self.project.root / "search" / "duplicate_groups.json").exists())

    def test_failure_logged_and_other_source_continues(self):
        write_yaml(self.project.root / "search_plan.yaml", {"searches": [
            {"id": "bad", "source": "bad", "query": "x"},
            {"id": "good", "source": "good", "query": "y"},
        ]})
        def fail(query, limit):
            raise TimeoutError("rate limited")
        result = SearchOrchestrator(self.project, {"bad": fail, "good": lambda q, n: [{"title": "Kept"}]}).run()
        self.assertEqual(result.failures, 1)
        self.assertEqual(len(result.records), 1)
        self.assertIn("TimeoutError", result.logs[0]["error"])

    def test_rerun_one_search_appends_raw_log_without_overwriting(self):
        write_yaml(self.project.root / "search_plan.yaml", {"searches": [{"id": "one", "source": "mock", "query": "x"}]})
        orchestrator = SearchOrchestrator(self.project, {"mock": lambda q, n: [{"title": "Record"}]})
        orchestrator.run(search_id="one")
        orchestrator.run(search_id="one")
        logs = (self.project.root / "search" / "search_log.jsonl").read_text().splitlines()
        raw = (self.project.root / "search" / "raw_records.jsonl").read_text().splitlines()
        self.assertEqual((len(logs), len(raw)), (2, 2))

    def test_disabled_search_is_not_called(self):
        write_yaml(self.project.root / "search_plan.yaml", {"searches": [{"id": "off", "source": "missing", "query": "x", "enabled": False}]})
        result = SearchOrchestrator(self.project, {}).run()
        self.assertEqual(result.records, [])


if __name__ == "__main__":
    unittest.main()
