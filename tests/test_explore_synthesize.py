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


if __name__ == "__main__":
    unittest.main()
