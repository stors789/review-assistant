import tempfile
import unittest
from pathlib import Path

from review_assistant.io_utils import append_jsonl, load_yaml, write_yaml
from review_assistant.project import ReviewProject
from review_assistant.review_audit import ReviewAuditor, _extraction_evidence_index
from review_assistant.review_evidence import ContradictionAnalyzer
from review_assistant.review_synthesis import (
    resolve_synthesis_plan,
    synthesize_review,
    validate_structured_section_output,
)
from review_assistant.studies import StudyExtractionStore, current_evidence, evidence_location_id


class ReviewBoundaryHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "project")
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        protocol["fulltext"] = {"requirement": "disabled"}
        protocol["synthesis"]["required_sections"] = ["Evidence"]
        write_yaml(self.project.root / "protocol.yaml", protocol)
        write_yaml(self.project.root / "synthesis_plan.yaml", {"sections": [{
            "section_id": "S01", "title": "Evidence", "evidence_filter": {"include_all_studies": True},
        }]})
        write_yaml(self.project.root / "search_plan.yaml", {"searches": [], "seed_records": [{"title": "Configured record"}]})

    def tearDown(self):
        self.tmp.cleanup()

    def _ingest(self, outcomes):
        _, studies, extracted = StudyExtractionStore(self.project).ingest({
            "publication": {"title": "Configured publication"},
            "studies": [{"label": "configured study", "fields": {}, "outcomes": outcomes}],
        })
        ContradictionAnalyzer(self.project).analyze()
        return studies[0].study_id, extracted

    @staticmethod
    def _writer(sentence, claim):
        return lambda **kwargs: {"section_text": sentence, "claims": [claim]}

    def test_current_index_and_superseded_references_are_distinguished(self):
        study_id, first = self._ingest([{
            "outcome_id": "outcome_old", "domain": "configured", "effect_direction": "increase",
            "support_relation": "supports", "evidence": [{"quote": "old verified quotation", "manual_verified": True}],
        }])
        old_evidence_id = first[0].evidence[0].evidence_id
        self._ingest([{
            "outcome_id": "outcome_new", "domain": "configured", "effect_direction": "increase",
            "support_relation": "supports", "evidence": [{"quote": "new verified quotation", "manual_verified": True}],
        }])
        current_outcome_index, current_evidence_index = _extraction_evidence_index(self.project)
        self.assertEqual(set(current_outcome_index), {"outcome_new"})
        self.assertEqual(set(current_evidence_index), {current_evidence(self.project)[0]["evidence_id"]})
        self.assertNotIn("outcome_old", current_outcome_index)
        self.assertNotIn(old_evidence_id, current_evidence_index)

        sentence = f"The configured result was reported [{study_id}]."
        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_id],
            "supporting_outcome_ids": ["outcome_old"],
            "supporting_evidence_refs": [old_evidence_id],
        }))
        summary = ReviewAuditor(self.project).run()
        self.assertIn("superseded_outcome_reference", summary["counts"])
        self.assertIn("superseded_evidence_reference", summary["counts"])

        new_evidence_id = current_evidence(self.project)[0]["evidence_id"]
        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_id],
            "supporting_outcome_ids": ["outcome_new"],
            "supporting_evidence_refs": [new_evidence_id],
        }))
        self.assertEqual(ReviewAuditor(self.project).run()["status"], "passed")

    def test_each_cited_study_needs_own_outcome_and_evidence_on_both_sides(self):
        first = [
            {"domain": "configured-a", "effect_direction": "increase", "support_relation": "supports", "evidence": [{"quote": "verified A quotation", "manual_verified": True}]},
        ]
        # Rebuild the fixture with both studies in one publication.
        self.project = ReviewProject.initialize_review(Path(self.tmp.name) / "combined")
        protocol = load_yaml(self.project.root / "protocol.yaml")
        protocol["screening"]["enforcement"] = "disabled"
        protocol["fulltext"] = {"requirement": "disabled"}
        protocol["synthesis"]["required_sections"] = ["Evidence"]
        write_yaml(self.project.root / "protocol.yaml", protocol)
        write_yaml(self.project.root / "synthesis_plan.yaml", {"sections": [{
            "section_id": "S01", "title": "Evidence", "evidence_filter": {"include_all_studies": True},
        }]})
        write_yaml(self.project.root / "search_plan.yaml", {"searches": [], "seed_records": [{"title": "Configured record"}]})
        _, studies, outcomes = StudyExtractionStore(self.project).ingest({
            "publication": {"title": "Combined publication"},
            "studies": [
                {"label": "A", "fields": {}, "outcomes": first},
                {"label": "B", "fields": {}, "outcomes": [{"domain": "configured-b", "effect_direction": "decrease", "support_relation": "contradicts", "evidence": [{"quote": "verified B quotation", "manual_verified": True}]}]},
            ],
        })
        ContradictionAnalyzer(self.project).analyze()
        study_a, study_b = [item.study_id for item in studies]
        outcome_a, outcome_b = outcomes
        evidence_a = outcome_a.evidence[0].evidence_id
        evidence_b = outcome_b.evidence[0].evidence_id
        sentence = f"The configured result included [{study_a}] and [{study_b}]."

        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_a, study_b],
            "supporting_outcome_ids": [outcome_a.outcome_id],
            "supporting_evidence_refs": [evidence_a],
        }))
        failed = ReviewAuditor(self.project).run()
        self.assertIn("supporting_study_without_outcome", failed["counts"])
        self.assertIn("supporting_study_without_evidence", failed["counts"])

        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_a],
            "contradicting_study_ids": [study_b],
            "supporting_outcome_ids": [outcome_a.outcome_id],
            "contradicting_outcome_ids": [outcome_b.outcome_id],
            "supporting_evidence_refs": [evidence_a],
            "contradicting_evidence_refs": [evidence_b],
        }))
        self.assertEqual(ReviewAuditor(self.project).run()["status"], "passed")

    def test_each_cited_outcome_needs_its_own_evidence(self):
        study_id, outcomes = self._ingest([
            {"domain": "configured-a", "effect_direction": "increase", "support_relation": "supports", "evidence": [{"quote": "verified first quotation", "manual_verified": True}]},
            {"domain": "configured-b", "effect_direction": "increase", "support_relation": "supports", "evidence": [{"quote": "verified second quotation", "manual_verified": True}]},
        ])
        sentence = f"The configured result was reported [{study_id}]."
        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_id],
            "supporting_outcome_ids": [outcomes[0].outcome_id, outcomes[1].outcome_id],
            "supporting_evidence_refs": [outcomes[0].evidence[0].evidence_id],
        }))
        failed = ReviewAuditor(self.project).run()
        self.assertIn("supporting_outcome_without_evidence", failed["counts"])
        self.assertIn("outcome_evidence_coverage_incomplete", failed["counts"])

        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_id],
            "supporting_outcome_ids": [outcomes[0].outcome_id, outcomes[1].outcome_id],
            "supporting_evidence_refs": [outcomes[0].evidence[0].evidence_id, outcomes[1].evidence[0].evidence_id],
        }))
        self.assertEqual(ReviewAuditor(self.project).run()["status"], "passed")

    def test_active_but_stale_run_reference_is_reported_separately(self):
        study_id, _ = self._ingest([{
            "domain": "configured", "effect_direction": "increase", "support_relation": "supports",
            "evidence": [{"quote": "verified current quotation", "manual_verified": True}],
        }])
        stale_location = {
            "quote": "verified stale quotation", "page": "9", "validation_status": "passed",
            "manual_verified": True, "extraction_run_id": "old-run",
        }
        stale_location["evidence_id"] = evidence_location_id(study_id, "outcome_stale", stale_location, 0)
        append_jsonl(self.project.root / "extraction" / "outcomes.jsonl", [{
            "outcome_id": "outcome_stale", "study_id": study_id, "domain": "configured",
            "effect_direction": "increase", "support_relation": "supports", "evidence": [stale_location],
            "extraction_run_id": "old-run", "status": "active",
        }])
        sentence = f"The configured result was reported [{study_id}]."
        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_id],
            "supporting_outcome_ids": ["outcome_stale"],
            "supporting_evidence_refs": [stale_location["evidence_id"]],
        }))
        summary = ReviewAuditor(self.project).run()
        self.assertIn("stale_extraction_reference", summary["counts"])

    def test_unknown_citation_and_outcome_filter_are_hard_failures(self):
        study_id, outcomes = self._ingest([
            {"domain": "target-domain", "effect_direction": "increase", "support_relation": "supports", "evidence": [{"quote": "verified target quotation", "manual_verified": True}]},
            {"domain": "other-domain", "effect_direction": "decrease", "support_relation": "contradicts", "evidence": [{"quote": "verified other quotation", "manual_verified": True}]},
        ])
        plan = load_yaml(self.project.root / "synthesis_plan.yaml")
        plan["sections"][0]["evidence_filter"] = {
            "outcome_domains": ["target-domain"], "effect_directions": ["increase"], "support_relations": ["supports"],
        }
        write_yaml(self.project.root / "synthesis_plan.yaml", plan)
        section = resolve_synthesis_plan(self.project)["sections"][0]
        sentence = f"The configured result was reported [{study_id}]."
        unknown = validate_structured_section_output(section, {
            "section_text": sentence + " Another report [study_fake].",
            "claims": [{"sentence": sentence, "supporting_study_ids": [study_id]}],
        }, [study_id])
        self.assertIn("study_fake", unknown["unknown_citation_tokens"])
        self.assertIn("unknown_study_citation_in_text", unknown["errors"])
        unknown_sentence = f"The configured result was reported [{study_id}] and [study_fake]."
        with self.assertRaisesRegex(ValueError, "coverage failed"):
            synthesize_review(self.project, self._writer(unknown_sentence, {
                "sentence": unknown_sentence, "supporting_study_ids": [study_id],
            }))

        synthesize_review(self.project, self._writer(sentence, {
            "sentence": sentence, "supporting_study_ids": [study_id],
            "supporting_outcome_ids": [outcomes[1].outcome_id],
            "supporting_evidence_refs": [outcomes[1].evidence[0].evidence_id],
        }))
        failed = ReviewAuditor(self.project).run()
        self.assertIn("supporting_outcome_outside_section_filter", failed["counts"])
        self.assertIn("effect_direction_outside_section", failed["counts"])
        self.assertIn("support_relation_outside_section", failed["counts"])


if __name__ == "__main__":
    unittest.main()
