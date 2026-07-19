import tempfile
import unittest
from pathlib import Path

from review_assistant.eligibility import resolve_eligibility, resolve_eligible_studies
from review_assistant.io_utils import append_jsonl, read_jsonl
from review_assistant.project import ReviewProject
from review_assistant.review_evidence import EvidenceMatrixBuilder
from review_assistant.review_audit import ReviewAuditor
from review_assistant.screening import ScreeningStore
from review_assistant.studies import StudyExtractionStore


class ReviewEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        append_jsonl(self.project.root / "search" / "deduplicated_records.jsonl", [
            {"record_id": "record_a", "title": "Included publication", "doi": "10.1/a"},
            {"record_id": "record_b", "title": "Excluded publication", "doi": "10.1/b"},
        ])
        screening = ScreeningStore(self.project)
        for record_id, decision in (("record_a", "include"), ("record_b", "exclude")):
            screening.decide(record_id, "title_abstract", "include", "tester")
            screening.decide(record_id, "fulltext", decision, "tester", reason_code="other" if decision == "exclude" else "")

    def tearDown(self):
        self.tmp.cleanup()

    def _ingest(self, record_id, title, doi):
        return StudyExtractionStore(self.project).ingest({
            "source_record_id": record_id, "source_file": f"{title}.pdf",
            "publication": {"title": title, "doi": doi},
            "studies": [{"label": title, "fields": {}, "outcomes": []}],
        })[1][0].study_id

    def test_explicit_record_publication_study_links_are_persisted(self):
        study_id = self._ingest("record_a", "Included publication", "10.1/a")
        publication_link = read_jsonl(self.project.root / "extraction" / "record_publication_links.jsonl")[-1]
        study_link = read_jsonl(self.project.root / "extraction" / "study_record_links.jsonl")[-1]
        self.assertEqual(publication_link["record_id"], "record_a")
        self.assertEqual(study_link["study_id"], study_id)
        self.assertEqual(study_link["link_method"], "explicit_input")
        self.assertEqual(study_link["confidence"], "exact")
        self.assertTrue(study_link["protocol_hash"])

    def test_fulltext_screening_gates_matrix_and_never_falls_back(self):
        included = self._ingest("record_a", "Included publication", "10.1/a")
        excluded = self._ingest("record_b", "Excluded publication", "10.1/b")
        self.assertEqual(resolve_eligible_studies(self.project), [included])
        rows = EvidenceMatrixBuilder(self.project).build()
        self.assertEqual([row["study_id"] for row in rows], [included])
        self.assertNotIn(excluded, {row["study_id"] for row in rows})

    def test_included_record_without_link_is_reported_not_backfilled(self):
        StudyExtractionStore(self.project).ingest({
            "publication": {"title": "Unmatched publication"},
            "studies": [{"fields": {}, "outcomes": []}],
        })
        result = resolve_eligibility(self.project)
        self.assertEqual(result.eligible_study_ids, set())
        self.assertIn("record_a", result.unlinked_included_record_ids)
        self.assertTrue(result.unlinked_study_ids)
        summary = ReviewAuditor(self.project).run()
        self.assertIn("included_record_lacking_study_link", summary["counts"])
        self.assertIn("study_lacking_source_record", summary["counts"])


if __name__ == "__main__":
    unittest.main()
