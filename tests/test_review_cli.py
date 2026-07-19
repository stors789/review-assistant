import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_assistant.cli import main
from review_assistant.io_utils import load_yaml, write_yaml


class ReviewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.assertEqual(main(["review", "init", str(self.root)]), 0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_and_project_validation(self):
        self.assertTrue((self.root / "project.yaml").exists())
        project = load_yaml(self.root / "project.yaml")
        self.assertEqual(project["mode"], "review")

    def test_dry_run_does_not_create_run(self):
        self.assertEqual(main(["review", "run", "--project", str(self.root), "--dry-run"]), 0)
        self.assertEqual(list((self.root / "runs").iterdir()), [])

    def test_stage_prerequisite_is_actionable_and_saved(self):
        code = main(["review", "run", "--project", str(self.root), "--from-stage", "screen", "--to-stage", "screen"])
        self.assertEqual(code, 1)
        run = next((self.root / "runs").iterdir())
        self.assertIn("Import screening decisions", (run / "errors.jsonl").read_text())

    def test_resume_reuses_failed_run_after_prerequisite_is_added(self):
        self.assertEqual(main(["review", "run", "--project", str(self.root), "--from-stage", "screen", "--to-stage", "screen"]), 1)
        history = self.root / "screening" / "decision_history.jsonl"
        history.write_text('{"record_id":"R1","stage":"title_abstract","decision":"include"}\n', encoding="utf-8")
        (self.root / "screening" / "prisma_counts.json").write_text('{}\n', encoding="utf-8")
        self.assertEqual(main(["review", "run", "--project", str(self.root), "--from-stage", "screen", "--to-stage", "screen", "--resume"]), 0)
        self.assertEqual(len(list((self.root / "runs").iterdir())), 1)

    def test_extract_matrix_analyze_synthesize_and_strict_audit(self):
        protocol = load_yaml(self.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        protocol["synthesis"]["required_sections"] = ["Evidence"]
        write_yaml(self.root / "protocol.yaml", protocol)
        write_yaml(self.root / "synthesis_plan.yaml", {"sections": [{
            "section_id": "S01", "title": "Evidence", "evidence_filter": {"include_all_studies": True},
        }]})
        extraction = Path(self.tmp.name) / "extraction.json"
        extraction.write_text(json.dumps({"publication": {"title": "P"}, "studies": [{"fields": {}, "outcomes": [{"domain": "x", "direction": "increase", "evidence": [{"quote": "q"}]}]}]}), encoding="utf-8")
        self.assertEqual(main(["review", "extract", "--project", str(self.root), "--input", str(extraction)]), 0)
        self.assertEqual(main(["review", "matrix", "build", "--project", str(self.root)]), 0)
        self.assertEqual(main(["review", "evidence", "analyze", "--project", str(self.root)]), 0)
        self.assertEqual(main(["review", "synthesize", "--project", str(self.root), "--offline-fixture-writer"]), 0)
        code = main(["review", "audit", "--project", str(self.root), "--strict"])
        self.assertIn(code, {0, 2})

    def test_bootstrap_marks_candidates_unconfirmed(self):
        explore = Path(self.tmp.name) / "explore"
        explore.mkdir()
        (explore / "outline.meta.json").write_text(json.dumps({"question": "Configured sample question"}))
        output = Path(self.tmp.name) / "formal"
        self.assertEqual(main(["review", "bootstrap", "--from-explore", str(explore), "--output", str(output)]), 0)
        candidate = load_yaml(output / "bootstrap_candidates.yaml")
        self.assertEqual(candidate["confirmation_status"], "unconfirmed")

    @patch("review_assistant.explore_synthesize.main")
    def test_explore_audit_reuses_existing_pipeline_and_findings(self, mocked_main):
        delegated = []
        mocked_main.side_effect = lambda: delegated.extend(sys.argv)
        self.assertEqual(main(["explore", "audit", "Collection", "-q", "question"]), 0)
        mocked_main.assert_called_once_with()
        self.assertIn("--skip-step1", delegated)


if __name__ == "__main__":
    unittest.main()
