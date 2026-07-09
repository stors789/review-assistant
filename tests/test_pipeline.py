import json
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch, ANY

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from review_assistant.pipeline import step1_extract_single
from review_assistant.errors import PDFExtractionError


class TestStep1ExtractSingle(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.text_cache_dir = self.tmp_path / "text_cache"
        self.text_cache_dir.mkdir(parents=True, exist_ok=True)
        self.findings_dir = self.tmp_path / "findings"
        self.findings_dir.mkdir(parents=True, exist_ok=True)

        self.pdf_path = self.tmp_path / "test_paper.pdf"
        self.pdf_path.write_bytes(b"fake pdf bytes")

        self.meta = {
            "title": "Test Paper Title",
            "authors": "Smith J, Doe A",
            "year": "2023",
            "ref_num": 1,
        }
        self.question = "What is the effect of X on Y?"
        self.model = "test-model"
        self.print_lock = threading.Lock()

        self.fake_hash = "a" * 64
        self.fake_cache_key = self.fake_hash[:16]
        self.fake_findings_cache_key = "fake_findings_key_123456"
        self.fake_text = "This is extracted paper text content."

    def tearDown(self):
        self.tmp.cleanup()

    @patch("review_assistant.pipeline.normalize_finding_relevance")
    @patch("review_assistant.pipeline.findings_cache_key")
    @patch("review_assistant.pipeline.build_extraction_prompt")
    @patch("review_assistant.pipeline.prepare_pdf_text")
    @patch("review_assistant.pipeline.file_sha256")
    @patch("review_assistant.pipeline.llm_client.call_json")
    def test_step1_skips_irrelevant_paper(
        self, mock_call_json, mock_file_sha256,
        mock_prepare, mock_build_prompt,
        mock_cache_key, mock_normalize,
    ):
        mock_file_sha256.return_value = self.fake_hash
        mock_prepare.return_value = self.fake_text
        mock_build_prompt.return_value = (self.fake_text, None)
        mock_cache_key.return_value = self.fake_findings_cache_key
        mock_call_json.return_value = {"relevant": False, "findings": []}
        mock_normalize.side_effect = lambda x: x

        result = step1_extract_single(
            client=MagicMock(),
            pdf_path=self.pdf_path,
            meta=self.meta,
            question=self.question,
            model=self.model,
            text_cache_dir=self.text_cache_dir,
            print_lock=self.print_lock,
            idx=1, total=1,
            findings_dir=None,
        )

        self.assertFalse(result["relevant"])
        self.assertEqual(result["findings"], [])

    @patch("review_assistant.pipeline.normalize_finding_relevance")
    @patch("review_assistant.pipeline.findings_cache_key")
    @patch("review_assistant.pipeline.build_extraction_prompt")
    @patch("review_assistant.pipeline.prepare_pdf_text")
    @patch("review_assistant.pipeline.file_sha256")
    @patch("review_assistant.pipeline.llm_client.call_json")
    def test_step1_extracts_findings_for_relevant_paper(
        self, mock_call_json, mock_file_sha256,
        mock_prepare, mock_build_prompt,
        mock_cache_key, mock_normalize,
    ):
        mock_file_sha256.return_value = self.fake_hash
        mock_prepare.return_value = self.fake_text
        mock_build_prompt.return_value = (self.fake_text, None)
        mock_cache_key.return_value = self.fake_findings_cache_key

        two_findings = {
            "relevant": True,
            "findings": [
                {
                    "claim_cn": "finding one",
                    "quote": "quote one",
                    "relevance_level": "direct",
                },
                {
                    "claim_cn": "finding two",
                    "quote": "quote two",
                    "relevance_level": "direct",
                },
            ],
        }
        mock_call_json.return_value = two_findings
        mock_normalize.side_effect = lambda x: x

        result = step1_extract_single(
            client=MagicMock(),
            pdf_path=self.pdf_path,
            meta=self.meta,
            question=self.question,
            model=self.model,
            text_cache_dir=self.text_cache_dir,
            print_lock=self.print_lock,
            idx=1, total=1,
            findings_dir=None,
        )

        self.assertTrue(result["relevant"])
        self.assertEqual(len(result["findings"]), 2)

    @patch("review_assistant.pipeline.normalize_finding_relevance")
    @patch("review_assistant.pipeline.findings_cache_key")
    @patch("review_assistant.pipeline.build_extraction_prompt")
    @patch("review_assistant.pipeline.llm_client.call_json")
    @patch("review_assistant.pipeline.file_sha256")
    @patch("review_assistant.extraction.file_sha256")
    @patch("review_assistant.extraction.extract_pdf_text")
    def test_step1_uses_cached_text_when_available(
        self, mock_extract_text, mock_ext_file_sha256, mock_pipe_file_sha256,
        mock_call_json, mock_build_prompt, mock_cache_key, mock_normalize,
    ):
        mock_pipe_file_sha256.return_value = self.fake_hash
        mock_ext_file_sha256.return_value = self.fake_hash

        cached_text = "cached text content from disk"
        cache_path = self.text_cache_dir / f"{self.fake_cache_key}.txt"
        cache_path.write_text(cached_text)

        mock_build_prompt.return_value = (cached_text, None)
        mock_cache_key.return_value = self.fake_findings_cache_key
        mock_call_json.return_value = {"relevant": True, "findings": []}
        mock_normalize.side_effect = lambda x: x

        result = step1_extract_single(
            client=MagicMock(),
            pdf_path=self.pdf_path,
            meta=self.meta,
            question=self.question,
            model=self.model,
            text_cache_dir=self.text_cache_dir,
            print_lock=self.print_lock,
            idx=1, total=1,
            findings_dir=None,
        )

        mock_extract_text.assert_not_called()
        self.assertTrue(result["relevant"])

    @patch("review_assistant.pipeline.llm_client.call_json")
    @patch("review_assistant.pipeline.findings_cache_key")
    @patch("review_assistant.pipeline.build_extraction_prompt")
    @patch("review_assistant.pipeline.prepare_pdf_text")
    @patch("review_assistant.pipeline.file_sha256")
    def test_step1_uses_cached_findings_when_available(
        self, mock_file_sha256, mock_prepare, mock_build_prompt,
        mock_cache_key, mock_call_json,
    ):
        mock_file_sha256.return_value = self.fake_hash
        mock_prepare.return_value = self.fake_text
        mock_build_prompt.return_value = (self.fake_text, None)
        mock_cache_key.return_value = self.fake_findings_cache_key

        cached_finding = {
            "claim_cn": "cached finding from disk",
            "quote": "cached quote",
            "relevance_level": "direct",
        }
        cached_result = {
            "relevant": True,
            "findings": [cached_finding],
            "file": "test_paper.pdf",
            "pdf_path": str(self.pdf_path),
            "ref_title": self.meta["title"],
            "ref_authors": self.meta["authors"],
            "ref_year": self.meta["year"],
            "ref_num": 1,
            "cache": {},
        }
        cache_path = self.findings_dir / f"{self.fake_findings_cache_key}.json"
        cache_path.write_text(json.dumps(cached_result, ensure_ascii=False))

        result = step1_extract_single(
            client=MagicMock(),
            pdf_path=self.pdf_path,
            meta=self.meta,
            question=self.question,
            model=self.model,
            text_cache_dir=self.text_cache_dir,
            print_lock=self.print_lock,
            idx=1, total=1,
            findings_dir=self.findings_dir,
            force_refresh=False,
        )

        mock_call_json.assert_not_called()
        self.assertTrue(result["relevant"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["claim_cn"], "cached finding from disk")

    @patch("review_assistant.pipeline.normalize_finding_relevance")
    @patch("review_assistant.pipeline.llm_client.call_json")
    @patch("review_assistant.pipeline.findings_cache_key")
    @patch("review_assistant.pipeline.build_extraction_prompt")
    @patch("review_assistant.pipeline.prepare_pdf_text")
    @patch("review_assistant.pipeline.file_sha256")
    def test_step1_force_refresh_bypasses_cache(
        self, mock_file_sha256, mock_prepare, mock_build_prompt,
        mock_cache_key, mock_call_json, mock_normalize,
    ):
        mock_file_sha256.return_value = self.fake_hash
        mock_prepare.return_value = self.fake_text
        mock_build_prompt.return_value = (self.fake_text, None)
        mock_cache_key.return_value = self.fake_findings_cache_key

        cached_result = {
            "relevant": True,
            "findings": [{"claim_cn": "stale cached finding", "relevance_level": "direct"}],
        }
        cache_path = self.findings_dir / f"{self.fake_findings_cache_key}.json"
        cache_path.write_text(json.dumps(cached_result, ensure_ascii=False))

        fresh_result = {
            "relevant": True,
            "findings": [{"claim_cn": "fresh finding", "relevance_level": "direct"}],
        }
        mock_call_json.return_value = fresh_result
        mock_normalize.side_effect = lambda x: x

        result = step1_extract_single(
            client=MagicMock(),
            pdf_path=self.pdf_path,
            meta=self.meta,
            question=self.question,
            model=self.model,
            text_cache_dir=self.text_cache_dir,
            print_lock=self.print_lock,
            idx=1, total=1,
            findings_dir=self.findings_dir,
            force_refresh=True,
        )

        mock_call_json.assert_called_once()
        self.assertEqual(result["findings"][0]["claim_cn"], "fresh finding")

    @patch("review_assistant.pipeline.file_sha256")
    @patch("review_assistant.pipeline.prepare_pdf_text")
    def test_step1_handles_pdf_extraction_failure(
        self, mock_prepare, mock_file_sha256,
    ):
        mock_file_sha256.return_value = self.fake_hash
        mock_prepare.side_effect = PDFExtractionError("PDF is encrypted")

        result = step1_extract_single(
            client=MagicMock(),
            pdf_path=self.pdf_path,
            meta=self.meta,
            question=self.question,
            model=self.model,
            text_cache_dir=self.text_cache_dir,
            print_lock=self.print_lock,
            idx=1, total=1,
            findings_dir=None,
        )

        self.assertFalse(result["relevant"])
        self.assertEqual(result["findings"], [])
        self.assertIn("PDF is encrypted", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
