import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "explore_synthesize.py"

sys.modules.setdefault("pymupdf", types.SimpleNamespace(open=lambda *args, **kwargs: None))
sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=object))
sys.modules.setdefault("zotero_reader", types.SimpleNamespace(ZoteroReader=object))

spec = importlib.util.spec_from_file_location("explore_synthesize", MODULE_PATH)
explore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(explore)


class ExploreSynthesizeTests(unittest.TestCase):
    def test_findings_cache_key_includes_content_question_and_model(self):
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"first")
            key1 = explore.findings_cache_key(pdf, "question", "model-a")
            key2 = explore.findings_cache_key(pdf, "question", "model-b")
            self.assertNotEqual(key1, key2)

            pdf.write_bytes(b"second")
            key3 = explore.findings_cache_key(pdf, "question", "model-a")
            self.assertNotEqual(key1, key3)

    def test_chunk_text_preserves_report_content(self):
        text = "\n\n".join(f"paragraph {i}" for i in range(20))
        chunks = explore.chunk_text(text, max_chars=60)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n\n".join(chunks), text)

    def test_step3_preserves_outline_order_with_parallel_writes(self):
        original_call_json = explore.call_json
        original_call_text = explore.call_text
        try:
            def fake_call_json(client, system, user, model, max_tokens=4096, retries=2):
                if "子议题: First" in user:
                    return {"matched_indices": [0]}
                if "子议题: Second" in user:
                    return {"matched_indices": [1]}
                return {"matched_indices": []}

            def fake_call_text(client, prompt, model, max_tokens=4096, retries=2, temperature=0):
                if "章节主题：First" in prompt:
                    time.sleep(0.05)
                    return "first content [1]"
                if "章节主题：Second" in prompt:
                    return "second content [1]"
                return ""

            explore.call_json = fake_call_json
            explore.call_text = fake_call_text

            outline = {
                "sections": [
                    {"heading": "Root", "subsections": [
                        {"heading": "First", "search_tags": {"topic": "a"}},
                        {"heading": "Second", "search_tags": {"topic": "b"}},
                    ]}
                ]
            }
            all_results = [{
                "file": "paper.pdf",
                "relevant": True,
                "ref_num": 1,
                "ref_title": "Paper",
                "ref_authors": "Author",
                "ref_year": "2024",
                "findings": [
                    {"cite_key": "Author et al., 2024", "claim_cn": "A", "quote": "Quote A", "tags": {"topic": "a"}},
                    {"cite_key": "Author et al., 2024", "claim_cn": "B", "quote": "Quote B", "tags": {"topic": "b"}},
                ],
            }]

            sections, _ = explore.step3_match_and_write(
                client_factory=lambda: object(),
                outline=outline,
                all_results=all_results,
                question="question",
                model="model",
                workers=2,
            )

            self.assertEqual([s["heading"] for s in sections], ["First", "Second"])
            self.assertEqual([s["content"] for s in sections], ["first content [1]", "second content [1]"])
        finally:
            explore.call_json = original_call_json
            explore.call_text = original_call_text

    def test_stable_json_sha256_is_order_independent_for_dicts(self):
        first = {"b": 2, "a": {"y": 1, "x": 0}}
        second = {"a": {"x": 0, "y": 1}, "b": 2}
        self.assertEqual(explore.stable_json_sha256(first), explore.stable_json_sha256(second))

    def test_cached_sections_loads_only_when_meta_matches(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sections_path = root / "sections.json"
            meta_path = root / "sections.meta.json"
            sections = [{"heading": "A", "content": "Body [1]"}]
            paper_refs = {1: {"authors": "Author", "title": "Title", "year": "2024"}}
            meta = explore.build_step_cache_meta(
                explore.SECTIONS_CACHE_VERSION,
                "question",
                "model",
                outline_sha256="outline",
                findings_sha256="findings",
            )

            explore.save_cached_sections(sections_path, meta_path, sections, paper_refs, meta)
            loaded = explore.load_cached_sections(sections_path, meta_path, meta)
            self.assertIsNotNone(loaded)
            loaded_sections, loaded_refs = loaded
            self.assertEqual(loaded_sections, sections)
            self.assertEqual(loaded_refs, paper_refs)

            stale_meta = dict(meta)
            stale_meta["findings_sha256"] = "changed"
            self.assertIsNone(explore.load_cached_sections(sections_path, meta_path, stale_meta))

    def test_cached_report_loads_only_when_meta_matches(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.md"
            meta_path = root / "report.meta.json"
            report = "# Report\n\nBody [1]"
            meta = explore.build_step_cache_meta(
                explore.REPORT_CACHE_VERSION,
                "question",
                "model",
                outline_sha256="outline",
                sections_sha256="sections",
                paper_refs_sha256="refs",
            )

            explore.save_cached_report(report_path, meta_path, report, meta)
            self.assertEqual(explore.load_cached_report(report_path, meta_path, meta), report)

            stale_meta = dict(meta)
            stale_meta["sections_sha256"] = "changed"
            self.assertIsNone(explore.load_cached_report(report_path, meta_path, stale_meta))

    def test_step7_uses_configured_model(self):
        class FakeCompletions:
            def __init__(self):
                self.models = []

            def create(self, **kwargs):
                self.models.append(kwargs["model"])
                content = "| A | B |\n|---|---|\n| x | y |"
                if "DIAGRAM" in str(kwargs.get("messages", "")):
                    content = "```mermaid\ngraph TD\nA-->B\n```"
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
                )

        class FakeClient:
            def __init__(self, completions):
                self.chat = types.SimpleNamespace(completions=completions)

        completions = FakeCompletions()
        original_call_json = explore.call_json
        try:
            explore.call_json = lambda client, system, user, model, max_tokens=4096, retries=2: {
                "title": "T",
                "row_dimension": "R",
                "column_dimension": "C",
                "cell_schema": "S",
            }
            explore.step7_summary(lambda: FakeClient(completions), "report", step7_model="custom-step7")
            self.assertTrue(completions.models)
            self.assertTrue(all(model == "custom-step7" for model in completions.models))
        finally:
            explore.call_json = original_call_json


if __name__ == "__main__":
    unittest.main()
