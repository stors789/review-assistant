import sys
import os
import unittest
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

# Add review_assistant package root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review_assistant import paper_breakdown

class PaperBreakdownTests(unittest.TestCase):
    def test_breakdown_paper(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"original_title": "Paper Title", "year": "2026"}'))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        res = paper_breakdown.breakdown_paper(mock_client, "some raw text", "model-name")
        self.assertEqual(res["original_title"], "Paper Title")
        self.assertEqual(res["year"], "2026")
        mock_client.chat.completions.create.assert_called_once()

    @patch("review_assistant.paper_breakdown.llm_client.get_client")
    @patch("review_assistant.paper_breakdown.extract_text")
    def test_process_pdfs_writes_json_and_summary_csv(self, mock_extract, mock_get_client):
        # Setup mocks
        mock_extract.return_value = "extracted text contents"
        
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"original_title": "Paper Title", "year": "2026", "authors": "John Smith"}'))
        ]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client


        with TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pdf1 = tmp_dir / "paper1.pdf"
            pdf1.write_bytes(b"dummy")
            
            # Add a second pdf that fails to test safety against CSV crash
            pdf2 = tmp_dir / "paper2.pdf"
            pdf2.write_bytes(b"dummy")
            
            # Make the second run fail
            def side_effect(client, text, model):
                if "paper2" in text or len(mock_extract.call_args_list) > 1:
                    raise Exception("API Error")
                return {"original_title": "Paper Title", "year": "2026", "authors": "John Smith"}
            
            with patch("review_assistant.paper_breakdown.breakdown_paper", side_effect=side_effect):
                paper_breakdown.process_pdfs(
                    pdf_paths=[pdf1, pdf2],
                    output_dir=tmp_dir / "out",
                    model="model",
                    api_key="key",
                    base_url="url",
                    workers=1
                )
                
            # Verify JSON was written for successful paper
            json_file = tmp_dir / "out" / "paper1.json"
            self.assertTrue(json_file.exists())
            data = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertEqual(data["original_title"], "Paper Title")
            
            # Verify CSV was written and has both rows (one successful, one error row fallback)
            csv_file = tmp_dir / "out" / "_summary.csv"
            self.assertTrue(csv_file.exists())
            
            with open(csv_file, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["file"], "paper1.pdf")
                self.assertEqual(rows[0]["original_title"], "Paper Title")
                self.assertEqual(rows[1]["file"], "paper2.pdf")
                # Failed row shouldn't crash, error details aren't in fields, should be empty string
                self.assertEqual(rows[1]["original_title"], "")

if __name__ == "__main__":
    unittest.main()
