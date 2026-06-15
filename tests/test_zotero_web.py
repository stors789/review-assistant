import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zotero_web.py"

spec = importlib.util.spec_from_file_location("zotero_web", MODULE_PATH)
zotero_web = importlib.util.module_from_spec(spec)
sys.modules["zotero_web"] = zotero_web
spec.loader.exec_module(zotero_web)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


class ZoteroWebTests(unittest.TestCase):
    def test_ensure_collection_path_creates_missing_leaf(self):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs.get("json")))
            if method == "GET":
                return FakeResponse(payload=[
                    {"key": "ROOT", "data": {"name": "电波", "parentCollection": False}},
                    {"key": "THETA", "data": {"name": "theta", "parentCollection": "ROOT"}},
                ])
            return FakeResponse(payload={"successful": {"0": {"key": "NEW"}}})

        original = zotero_web.requests.request
        try:
            zotero_web.requests.request = fake_request
            client = zotero_web.ZoteroWebClient("key", "user", "123")
            key = client.ensure_collection_path("电波 > theta > metabolic coupling")
            self.assertEqual(key, "NEW")
            self.assertEqual(calls[-1][2][0]["name"], "metabolic coupling")
            self.assertEqual(calls[-1][2][0]["parentCollection"], "THETA")
        finally:
            zotero_web.requests.request = original

    def test_ensure_collection_path_can_refuse_creation(self):
        original = zotero_web.requests.request
        try:
            zotero_web.requests.request = lambda *args, **kwargs: FakeResponse(payload=[])
            client = zotero_web.ZoteroWebClient("key", "group", "99")
            with self.assertRaises(zotero_web.ZoteroWebError):
                client.ensure_collection_path("Missing", create=False)
        finally:
            zotero_web.requests.request = original

    def test_paper_to_zotero_item_maps_metadata(self):
        paper = {
            "title": "Title",
            "authors": [{"name": "Doe John"}, {"name": "Smith, Jane"}],
            "year": 2025,
            "externalIds": {"DOI": "10.1000/test"},
            "journal": {"name": "Journal"},
            "abstract": "Abstract",
            "_zotero_tags": ["topic", "screen:A"],
        }
        item = zotero_web.paper_to_zotero_item(paper, "COLL", ["fallback"])
        self.assertEqual(item["itemType"], "journalArticle")
        self.assertEqual(item["collections"], ["COLL"])
        self.assertEqual(item["DOI"], "10.1000/test")
        self.assertEqual(item["publicationTitle"], "Journal")
        self.assertEqual(item["tags"], [{"tag": "topic"}, {"tag": "screen:A"}])
        self.assertEqual(item["creators"][0]["lastName"], "John")
        self.assertEqual(item["creators"][1]["lastName"], "Smith")

    def test_create_items_batches_by_50(self):
        batch_sizes = []

        def fake_request(method, url, **kwargs):
            payload = kwargs.get("json", [])
            batch_sizes.append(len(payload))
            return FakeResponse(payload={"successful": {str(i): {"key": f"K{i}"} for i in range(len(payload))}})

        original = zotero_web.requests.request
        try:
            zotero_web.requests.request = fake_request
            client = zotero_web.ZoteroWebClient("key", "user", "123")
            papers = [{"title": f"P{i}", "externalIds": {"DOI": f"10/{i}"}} for i in range(55)]
            result = client.create_items(papers, "COLL", ["tag"])
            self.assertEqual(batch_sizes, [50, 5])
            self.assertEqual(len(result["successful"]), 55)
        finally:
            zotero_web.requests.request = original

    def test_wait_for_local_dois_success_and_timeout(self):
        calls = 0

        def fetch():
            nonlocal calls
            calls += 1
            return {"10/a"} if calls > 1 else set()

        original_sleep = time.sleep
        try:
            zotero_web.time.sleep = lambda _: None
            found, missing = zotero_web.wait_for_local_dois(fetch, {"10/a"}, timeout=1, interval=0)
            self.assertEqual(found, {"10/a"})
            self.assertEqual(missing, set())

            found, missing = zotero_web.wait_for_local_dois(lambda: set(), {"10/b"}, timeout=0, interval=0)
            self.assertEqual(found, set())
            self.assertEqual(missing, {"10/b"})
        finally:
            zotero_web.time.sleep = original_sleep


if __name__ == "__main__":
    unittest.main()
