import unittest
from pathlib import Path
from explore_synthesize import (
    _split_report_refs,
    verify_references_programmatic,
    normalize_table_views,
    choose_table_view,
    should_stop_after
)

class TestExploreSynthesizeHelpers(unittest.TestCase):

    def test_split_report_refs(self):
        report = "This is body [1].\n## 参考文献\n[1] Author. Title. 2020."
        body, refs = _split_report_refs(report)
        self.assertEqual(body, "This is body [1].")
        self.assertEqual(refs, "\n## 参考文献\n[1] Author. Title. 2020.")

        report_no_refs = "This is body with no references."
        body, refs = _split_report_refs(report_no_refs)
        self.assertEqual(body, report_no_refs)
        self.assertEqual(refs, "")

    def test_verify_references_programmatic(self):
        paper_refs = {
            1: {"authors": "Smith", "title": "Alpha Brain Waves", "year": "2020"},
            2: {"authors": "Jones", "title": "Beta Waves", "year": "2021"}
        }

        # Perfect match
        report_ok = "Body [1] and [2].\n## 参考文献\n[1] Smith. Alpha Brain Waves. 2020.\n[2] Jones. Beta Waves. 2021."
        self.assertEqual(verify_references_programmatic(report_ok, paper_refs), "")

        # Missing ref in list
        report_missing = "Body [1] and [2].\n## 参考文献\n[1] Smith. Alpha Brain Waves. 2020."
        issues = verify_references_programmatic(report_missing, paper_refs)
        self.assertIn("正文引用缺少参考文献条目: [2]", issues)

        # Unknown body ref
        report_unknown = "Body [1] and [3].\n## 参考文献\n[1] Smith. Alpha Brain Waves. 2020.\n[3] Unknown. Title. 2022."
        issues = verify_references_programmatic(report_unknown, paper_refs)
        self.assertIn("正文引用编号不在 paper_refs 中: [3]", issues)

    def test_normalize_table_views(self):
        raw = [
            {
                "title": "Good View",
                "row_dimension": "A",
                "column_dimension": "B",
                "estimated_direct_evidence_coverage": 0.85
            },
            {
                # Missing column dimension
                "title": "Bad View",
                "row_dimension": "A"
            }
        ]
        norm = normalize_table_views(raw)
        self.assertEqual(len(norm), 1)
        self.assertEqual(norm[0]["title"], "Good View")
        self.assertEqual(norm[0]["estimated_direct_evidence_coverage"], 0.85)

    def test_choose_table_view(self):
        views = [
            {
                "title": "View 1",
                "row_dimension": "A",
                "column_dimension": "B",
                "estimated_direct_evidence_coverage": 0.5,
                "coverage_rationale": "ok"
            },
            {
                "title": "View 2",
                "row_dimension": "A",
                "column_dimension": "B",
                "estimated_direct_evidence_coverage": 0.9,
                "coverage_rationale": "better"
            }
        ]
        chosen = choose_table_view(views)
        self.assertEqual(chosen["title"], "View 2")

    def test_should_stop_after(self):
        self.assertTrue(should_stop_after("step1", "step1"))
        self.assertFalse(should_stop_after("step1", "step3"))
        self.assertFalse(should_stop_after("step1", None))

if __name__ == "__main__":
    unittest.main()
