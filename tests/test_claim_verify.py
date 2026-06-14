import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import claim_verify

class ClaimVerifyTests(unittest.TestCase):
    @patch("claim_verify.llm_client")
    def test_decompose_claims(self, mock_llm):
        mock_llm.call_json.return_value = {"claims": ["Claim 1", "Claim 2"]}
        mock_client = MagicMock()
        
        claims = claim_verify.decompose_claims(mock_client, "Sample paragraph text", "model-name")
        self.assertEqual(claims, ["Claim 1", "Claim 2"])
        mock_llm.call_json.assert_called_once_with(
            mock_client,
            "你是一个输出 JSON 的助手。",
            claim_verify.CLAIM_DECOMPOSE_PROMPT + "\n\n段落：\nSample paragraph text",
            "model-name",
            2048
        )

    @patch("claim_verify.llm_client")
    def test_match_papers(self, mock_llm):
        mock_llm.call_json.return_value = {"relevant_indices": [0]}
        mock_client = MagicMock()
        papers = [
            {"title": "Paper 1", "authors": "Author 1", "journal": "J1", "date": "2026", "doi": "doi1"},
            {"title": "Paper 2", "authors": "Author 2", "journal": "J2", "date": "2025", "doi": "doi2"},
        ]
        
        matches = claim_verify.match_papers(mock_client, "Target claim", papers, "model-name", 1)
        self.assertEqual(matches, [0])
        mock_llm.call_json.assert_called_once()

    @patch("claim_verify.llm_client")
    def test_verify_claim(self, mock_llm):
        mock_llm.call_json.return_value = {"support": "完全支持", "evidence_cn": "摘录证据", "reasoning": "分析"}
        mock_client = MagicMock()
        paper = {"title": "Paper 1", "authors": "Author 1", "journal": "J1", "date": "2026", "doi": "doi1", "text": "Paper full text"}
        
        res = claim_verify.verify_claim(mock_client, "Target claim", paper, "model-name")
        self.assertEqual(res["support"], "完全支持")
        mock_llm.call_json.assert_called_once()

if __name__ == "__main__":
    unittest.main()
