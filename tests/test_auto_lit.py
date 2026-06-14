import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "auto_lit.py"

sys.modules.setdefault("requests", types.SimpleNamespace(get=lambda *args, **kwargs: None))
sys.modules.setdefault("zotero_reader", types.SimpleNamespace(ZoteroReader=object))

spec = importlib.util.spec_from_file_location("auto_lit", MODULE_PATH)
auto_lit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto_lit)


class AutoLitScreenTests(unittest.TestCase):
    def test_screen_keeps_recent_low_citation_direct_match(self):
        paper = {
            "title": "Theta EEG coupling with FDG PET glucose metabolism in prodromal Alzheimer disease",
            "abstract": "Human older adult participants showed correlations between theta power and glucose metabolism.",
            "year": 2025,
            "citationCount": 0,
            "journal": {"name": "NeuroImage"},
        }
        result = auto_lit.screen_paper(paper, min_relevance=4)
        self.assertTrue(result["keep"])
        self.assertEqual(result["tier"], "A")
        self.assertGreaterEqual(result["score"], 4)

    def test_screen_rejects_older_low_relevance_low_citation_paper(self):
        paper = {
            "title": "EEG classification method for task performance",
            "abstract": "A signal processing method is evaluated in a small dataset.",
            "year": 2012,
            "citationCount": 1,
            "journal": {"name": "Conference Proceedings"},
        }
        result = auto_lit.screen_paper(paper, min_relevance=4)
        self.assertFalse(result["keep"])
        self.assertEqual(result["tier"], "C")

    def test_screen_penalizes_excluded_domains(self):
        paper = {
            "title": "Theta EEG and PET metabolism in pediatric epilepsy",
            "abstract": "Children with seizures were evaluated using PET and EEG.",
            "year": 2018,
            "citationCount": 20,
            "journal": {"name": "Epilepsy Research"},
        }
        result = auto_lit.screen_paper(paper, min_relevance=4)
        self.assertIn("C", result["tier"])
        self.assertTrue(any(reason.startswith("exclude:") for reason in result["reasons"]))

    def test_term_matching_uses_word_boundaries_for_short_terms(self):
        self.assertNotIn("rat", auto_lit._matched_terms("regional perfusion alteration", {"rat"}))
        self.assertIn("rat", auto_lit._matched_terms("rat model of metabolism", {"rat"}))

    def test_ris_splits_screen_tags_into_keywords(self):
        paper = {
            "title": "Theta EEG BOLD coupling",
            "year": 2024,
            "authors": [{"name": "A. Author"}],
            "externalIds": {"DOI": "10.1000/test"},
        }
        ris = auto_lit._to_ris(paper, 1, "theta-metabolic-coupling; screen:A; score:7")
        self.assertIn("KW  - theta-metabolic-coupling", ris)
        self.assertIn("KW  - screen:A", ris)
        self.assertIn("KW  - score:7", ris)


if __name__ == "__main__":
    unittest.main()
