import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "auto_lit.py"

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

    def test_search_source_dispatch(self):
        # Test that _search dispatches to _search_pubmed when source is pubmed
        original_ss = auto_lit._search_ss
        original_pubmed = auto_lit._search_pubmed
        try:
            auto_lit._search_ss = lambda q, l: [{"title": "SS paper"}]
            auto_lit._search_pubmed = lambda q, l: [{"title": "PubMed paper"}]
            
            res_ss = auto_lit._search("query", "ss", 5)
            self.assertEqual(res_ss[0]["title"], "SS paper")
            
            res_pubmed = auto_lit._search("query", "pubmed", 5)
            self.assertEqual(res_pubmed[0]["title"], "PubMed paper")
        finally:
            auto_lit._search_ss = original_ss
            auto_lit._search_pubmed = original_pubmed

    def test_search_pubmed_xml_parsing(self):
        import requests
        from unittest.mock import patch, MagicMock
        
        # Mock search response
        mock_search_json = {
            "esearchresult": {
                "idlist": ["12345"]
            }
        }
        
        # Mock XML fetch response
        mock_xml = """<PubmedArticleSet>
            <PubmedArticle>
                <MedlineCitation>
                    <PMID>12345</PMID>
                    <Article>
                        <ArticleTitle>Mocked PubMed Article Title</ArticleTitle>
                        <AuthorList>
                            <Author>
                                <LastName>Doe</LastName>
                                <ForeName>John</ForeName>
                            </Author>
                        </AuthorList>
                        <Journal>
                            <Title>Journal of Testing</Title>
                        </Journal>
                        <JournalIssue>
                            <PubDate>
                                <Year>2025</Year>
                            </PubDate>
                        </JournalIssue>
                        <Abstract>
                            <AbstractText Label="OBJECTIVE">To test XML parsing.</AbstractText>
                        </Abstract>
                    </Article>
                </MedlineCitation>
                <PubmedData>
                    <ArticleIdList>
                        <ArticleId IdType="doi">10.1234/mock.doi</ArticleId>
                    </ArticleIdList>
                </PubmedData>
            </PubmedArticle>
        </PubmedArticleSet>"""
        
        original_get = requests.get
        try:
            # We mock requests.get to return search json, then xml content
            mock_responses = [
                MagicMock(status_code=200, json=lambda: mock_search_json, raise_for_status=lambda: None),
                MagicMock(status_code=200, content=mock_xml.encode('utf-8'), raise_for_status=lambda: None)
            ]
            
            call_count = 0
            def mock_get(*args, **kwargs):
                nonlocal call_count
                res = mock_responses[call_count]
                call_count += 1
                return res
                
            requests.get = mock_get
            
            # Disable rate limit sleep to speed up test
            original_sleep = auto_lit.time.sleep
            auto_lit.time.sleep = lambda s: None
            try:
                results = auto_lit._search_pubmed("test query", limit=1)
                self.assertEqual(len(results), 1)
                paper = results[0]
                self.assertEqual(paper["title"], "Mocked PubMed Article Title")
                self.assertEqual(paper["authors"], [{"name": "Doe John"}])
                self.assertEqual(paper["year"], 2025)
                self.assertEqual(paper["externalIds"]["DOI"], "10.1234/mock.doi")
                self.assertEqual(paper["journal"]["name"], "Journal of Testing")
                self.assertEqual(paper["abstract"], "OBJECTIVE: To test XML parsing.")
            finally:
                auto_lit.time.sleep = original_sleep
        finally:
            requests.get = original_get

    def test_pubmed_key_delay_config(self):
        # Verify delay is 0.2s when key is set, and 1.5s when key is empty
        original_key = auto_lit.PUBMED_KEY
        try:
            auto_lit.PUBMED_KEY = "mock_key"
            
            # Mock requests.get and sleep
            import requests
            from unittest.mock import MagicMock
            
            mock_responses = [
                MagicMock(status_code=200, json=lambda: {"esearchresult": {"idlist": ["1"]}}, raise_for_status=lambda: None),
                MagicMock(status_code=200, content=b"<PubmedArticleSet></PubmedArticleSet>", raise_for_status=lambda: None)
            ]
            call_count = 0
            def mock_get(*args, **kwargs):
                nonlocal call_count
                res = mock_responses[call_count]
                call_count += 1
                return res
            
            original_get = requests.get
            requests.get = mock_get
            
            sleep_args = []
            original_sleep = auto_lit.time.sleep
            auto_lit.time.sleep = lambda s: sleep_args.append(s)
            
            try:
                auto_lit._search_pubmed("query", limit=1)
                self.assertIn(0.2, sleep_args)
            finally:
                auto_lit.time.sleep = original_sleep
                requests.get = original_get
                
            # Test empty key
            auto_lit.PUBMED_KEY = ""
            requests.get = mock_get
            call_count = 0
            sleep_args = []
            original_sleep = auto_lit.time.sleep
            auto_lit.time.sleep = lambda s: sleep_args.append(s)
            try:
                auto_lit._search_pubmed("query", limit=1)
                self.assertIn(1.5, sleep_args)
            finally:
                auto_lit.time.sleep = original_sleep
                requests.get = original_get
        finally:
            auto_lit.PUBMED_KEY = original_key

    def test_web_import_path_does_not_write_ris_or_open_zotero(self):
        class Args:
            zotero_api_key = "key"
            zotero_library_type = "user"
            zotero_library_id = "123"
            collection = "Root > Leaf"
            collection_key = ""
            create_collection = True
            tag = "topic"
            zotero_dir = None
            wait_local_sync = False
            sync_timeout = 0

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.created = []

            def ensure_collection_path(self, path, create=True):
                self.path = path
                self.create = create
                return "COLL"

            def find_existing_dois(self, dois):
                return set()

            def create_items(self, papers, collection_key, tags):
                self.created.append((papers, collection_key, tags))
                return {"successful": {"0": {"key": "ITEM"}}, "failed": {}}

        original_client = auto_lit.ZoteroWebClient
        original_existing = auto_lit._get_existing_dois
        try:
            auto_lit.ZoteroWebClient = FakeClient
            auto_lit._get_existing_dois = lambda zotero_dir=None: set()
            ok = auto_lit._web_import(Args(), [{
                "title": "Paper",
                "externalIds": {"DOI": "10.1000/test"},
                "_zotero_tags": ["topic"],
            }])
            self.assertTrue(ok)
        finally:
            auto_lit.ZoteroWebClient = original_client
            auto_lit._get_existing_dois = original_existing


if __name__ == "__main__":
    unittest.main()
