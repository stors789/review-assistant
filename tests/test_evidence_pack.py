import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from review_assistant.evidence_pack import (
    split_text_chunks,
    build_keyword_windows,
    build_boundary_chunks,
    _merge_ranges,
    normalize_finding_relevance,
    _normalize_finding_schema,
    cosine_similarity,
)


IMRAD_TEXT = """Abstract
This study investigates the relationship between sleep and memory consolidation.
Our results show that NREM sleep enhances declarative memory retention.

Introduction
Sleep is known to play a critical role in memory consolidation. Numerous studies
have demonstrated that sleep, particularly slow-wave sleep, facilitates synaptic
plasticity and memory replay. However, the precise mechanisms remain unclear.

Methods
We recruited 30 healthy participants (15 male, 15 female, ages 20-35). All
participants completed a declarative memory task before and after a 90-minute
nap with polysomnography recording.

Results
Participants who achieved NREM stage 3 sleep showed significantly enhanced
memory recall compared to those who remained in lighter sleep stages.
Mean improvement was 23% (p < 0.001).

Discussion
Our findings support the active consolidation hypothesis. The correlation
between slow-wave activity and memory improvement suggests a causal role
for NREM-specific mechanisms in declarative memory consolidation.

Conclusion
These results demonstrate that NREM sleep enhances declarative memory
consolidation, with potential clinical implications for sleep disorders.

References
1. Marshall & Born (2007). The contribution of sleep to hippocampus-dependent memory consolidation.
"""

LONG_TEXT = ("This is a long text for testing boundary chunks. " * 500)

SHORT_TEXT = "Short text that is well under 6000 characters."


class TestSplitTextChunks(unittest.TestCase):
    """Tests for split_text_chunks - section detection and fallback windows."""

    def test_detected_imrad_sections_produce_correct_section_labels(self):
        chunks = split_text_chunks(IMRAD_TEXT)
        sources = [c["source"] for c in chunks]
        sections = {c["section"] for c in chunks}
        self.assertIn("detected_section", sources)
        self.assertIn("abstract", sections)
        self.assertIn("introduction", sections)
        self.assertIn("methods", sections)
        self.assertIn("results", sections)
        self.assertIn("discussion", sections)
        self.assertIn("conclusion", sections)
        self.assertNotIn("fallback_window", sources)

    def test_detected_sections_have_non_overlapping_ranges(self):
        chunks = split_text_chunks(IMRAD_TEXT)
        for i in range(len(chunks)):
            for j in range(i + 1, len(chunks)):
                a = chunks[i]
                b = chunks[j]
                self.assertFalse(
                    a["char_start"] < b["char_end"] and b["char_start"] < a["char_end"],
                    f"Overlap between chunk {a['chunk_id']} [{a['char_start']}-{a['char_end']}] "
                    f"and {b['chunk_id']} [{b['char_start']}-{b['char_end']}]",
                )

    def test_detected_sections_cover_full_text(self):
        chunks = split_text_chunks(IMRAD_TEXT)
        sections_sorted = sorted(chunks, key=lambda c: c["char_start"])
        self.assertEqual(sections_sorted[0]["char_start"], 0, "First chunk should start at offset 0")
        self.assertEqual(sections_sorted[-1]["char_end"], len(IMRAD_TEXT),
                         "Last chunk should end at full text length")

    def test_plain_text_without_headings_uses_fallback_window(self):
        plain = " ".join(["word"] * 2000)
        chunks = split_text_chunks(plain)
        sources = {c["source"] for c in chunks}
        self.assertIn("fallback_window", sources)
        self.assertNotIn("detected_section", sources)
        for c in chunks:
            self.assertEqual(c["section"], "unknown")

    def test_fallback_windows_have_correct_step_overlap(self):
        plain = "x" * 15000
        chunks = split_text_chunks(plain, window_chars=6000, overlap_chars=800)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertEqual(c["source"], "fallback_window")
        self.assertEqual(chunks[0]["char_start"], 0)
        expected_step = 6000 - 800
        self.assertEqual(chunks[1]["char_start"], expected_step)
        for c in chunks:
            chunk_len = c["char_end"] - c["char_start"]
            self.assertLessEqual(chunk_len, 6000)

    def test_section_header_with_numbered_format(self):
        numbered = """Abstract
Here is a short abstract.

1. Introduction
We explore the topic.

2.1 Methods Overview
This section has a numbered sub-heading.

3. Results and Analysis
The main findings are presented here.
"""
        chunks = split_text_chunks(numbered)
        heads = [c["heading"] for c in chunks if c["source"] == "detected_section"]
        self.assertIn("Abstract", heads)

    def test_canonical_section_detects_mixed_case_aliases(self):
        text = "ABSTRACT\nAbstract paragraph.\n\nSUMMARY\nAnother abstract.\n\nINTRODUCTION\nIntro text.\n\nMETHODS\nHow we did it."
        chunks = split_text_chunks(text)
        sections = {c["section"] for c in chunks}
        self.assertIn("abstract", sections, "Both ABSTRACT and SUMMARY map to abstract")
        self.assertIn("introduction", sections)
        self.assertIn("methods", sections)


class TestBuildKeywordWindows(unittest.TestCase):
    """Tests for build_keyword_windows."""

    def test_known_keywords_produce_correct_windows(self):
        text = "alpha " * 500 + "beta target gamma " + "delta " * 500
        terms = ["target"]
        windows = build_keyword_windows(text, terms)
        self.assertEqual(len(windows), 1, "Should find exactly one keyword window")
        self.assertEqual(windows[0]["source"], "keyword_window")
        self.assertIn("target", windows[0]["text"])

    def test_multiple_keyword_hits_produce_correct_count(self):
        # Ensure keywords are far enough apart (gap > 300) to produce separate windows
        text = ("aaaa " * 1500 + "keyword1 " + "bbbb " * 1500 +
                "cccc " * 1500 + "keyword2 " + "dddd " * 1500)
        terms = ["keyword1", "keyword2"]
        windows = build_keyword_windows(text, terms)
        self.assertEqual(len(windows), 2, "Should find two keyword windows from two distinct hits")

    def test_nearby_keyword_hits_merge_into_single_window(self):
        text = "aa " * 200 + "target1 target2 " + "bb " * 200
        terms = ["target1", "target2"]
        windows = build_keyword_windows(text, terms)
        self.assertEqual(len(windows), 1, "Two nearby hits should be merged into one window")

    def test_far_apart_keyword_hits_remain_separate(self):
        text = "aa " * 1000 + "target1 " + "bb " * 1500 + "target2 " + "cc " * 1000
        terms = ["target1", "target2"]
        windows = build_keyword_windows(text, terms)
        self.assertEqual(len(windows), 2, "Two distant hits should produce two separate windows")

    def test_no_matching_terms_returns_empty_list(self):
        text = "alpha beta gamma delta epsilon " * 50
        terms = ["nonexistent", "not_found"]
        windows = build_keyword_windows(text, terms)
        self.assertEqual(windows, [])

    def test_empty_terms_list_returns_empty(self):
        text = "some text with content"
        windows = build_keyword_windows(text, [])
        self.assertEqual(windows, [])

    def test_empty_text_returns_empty(self):
        windows = build_keyword_windows("", ["anything"])
        self.assertEqual(windows, [])

    def test_case_insensitive_matching(self):
        text = "Some UPPERCASE content with MixedCase words to find Target."
        terms = ["TARGET", "uppercase", "mixedcase"]
        windows = build_keyword_windows(text, terms, window_radius=100)
        self.assertEqual(len(windows), 1, "Case-insensitive hits should merge into one window")


class TestBuildBoundaryChunks(unittest.TestCase):
    """Tests for build_boundary_chunks."""

    def test_long_text_produces_front_and_tail_chunks(self):
        text = "A" * 18000
        chunks = build_boundary_chunks(text, front_chars=6000, tail_chars=6000)
        self.assertEqual(len(chunks), 2)
        sources = {c["source"] for c in chunks}
        self.assertEqual(sources, {"front_matter", "tail"})

    def test_front_chunk_truncated_to_approximately_6000_chars(self):
        text = "A" * 18000
        chunks = build_boundary_chunks(text, front_chars=6000, tail_chars=6000)
        front = chunks[0]
        self.assertEqual(front["source"], "front_matter")
        self.assertLessEqual(len(front["text"]), 6000)
        self.assertEqual(front["char_start"], 0)
        self.assertEqual(front["char_end"], 6000)

    def test_tail_chunk_is_last_6000_chars(self):
        text = "A" * 18000
        chunks = build_boundary_chunks(text, front_chars=6000, tail_chars=6000)
        tail = chunks[1]
        self.assertEqual(tail["source"], "tail")
        self.assertLessEqual(len(tail["text"]), 6000)
        self.assertEqual(tail["char_end"], 18000)
        self.assertEqual(tail["char_start"], 12000)

    def test_short_text_below_front_chars_returns_only_front(self):
        text = "x" * 3000
        chunks = build_boundary_chunks(text, front_chars=6000, tail_chars=6000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["source"], "front_matter")
        self.assertEqual(len(chunks[0]["text"]), 3000)

    def test_text_exactly_at_front_chars_boundary(self):
        text = "x" * 6000
        chunks = build_boundary_chunks(text, front_chars=6000, tail_chars=6000)
        self.assertEqual(len(chunks), 1, "Exactly 6000 chars should only produce front chunk")
        self.assertEqual(chunks[0]["source"], "front_matter")

    def test_empty_text_returns_empty(self):
        chunks = build_boundary_chunks("", front_chars=6000, tail_chars=6000)
        self.assertEqual(chunks, [])


class TestMergeRanges(unittest.TestCase):
    """Tests for _merge_ranges with overlapping, near, and far ranges."""

    def test_overlapping_ranges_merge(self):
        ranges = [(0, 100), (50, 200)]
        merged = _merge_ranges(ranges)
        self.assertEqual(merged, [(0, 200)])

    def test_contained_ranges_merge_to_outer(self):
        ranges = [(10, 200), (50, 100)]
        merged = _merge_ranges(ranges)
        self.assertEqual(merged, [(10, 200)])

    def test_near_ranges_within_max_gap_merge(self):
        ranges = [(0, 200), (450, 600)]
        merged = _merge_ranges(ranges, max_gap=300)
        self.assertEqual(merged, [(0, 600)], "Gap of 250 < 300 should trigger merge")

    def test_far_ranges_beyond_max_gap_stay_separate(self):
        ranges = [(0, 200), (550, 700)]
        merged = _merge_ranges(ranges, max_gap=300)
        self.assertEqual(merged, [(0, 200), (550, 700)], "Gap of 350 > 300 should keep separate")

    def test_multiple_ranges_cascade_merge(self):
        # With max_gap=300, gap between (400,700) = 300, satisifes start(700) <= end(400)+300
        # so all four ranges cascade-merge.
        ranges = [(0, 50), (100, 150), (300, 400), (700, 800)]
        merged = _merge_ranges(ranges, max_gap=300)
        self.assertEqual(merged, [(0, 800)])
        # With max_gap=299 the last range stays separate
        self.assertEqual(_merge_ranges(ranges, max_gap=299), [(0, 400), (700, 800)])

    def test_unsorted_input_is_sorted_first(self):
        ranges = [(500, 600), (0, 100)]
        merged = _merge_ranges(ranges)
        self.assertEqual(merged, [(0, 100), (500, 600)])

    def test_negative_start_values_clamped_to_zero(self):
        ranges = [(-50, 100), (200, 300)]
        # Clamped to (0,100) and (200,300); gap=100 < default max_gap=300, so they merge
        merged = _merge_ranges(ranges)
        self.assertEqual(merged, [(0, 300)])
        # With tighter max_gap they stay separate
        self.assertEqual(_merge_ranges(ranges, max_gap=99), [(0, 100), (200, 300)])

    def test_zero_length_ranges_discarded(self):
        ranges = [(10, 10), (100, 200)]
        merged = _merge_ranges(ranges)
        self.assertEqual(merged, [(100, 200)])


class TestNormalizeFindingRelevance(unittest.TestCase):
    """Tests for normalize_finding_relevance and _normalize_finding_schema."""

    def test_all_background_findings_set_relevant_true(self):
        result = {
            "findings": [
                {
                    "relevance_level": "background",
                    "claim_cn": "Background context about sleep.",
                    "tags": {},
                },
                {
                    "relevance_level": "background",
                    "claim_cn": "Another background note.",
                    "tags": {"domain": "neuroscience"},
                },
            ]
        }
        normalized = normalize_finding_relevance(result)
        self.assertTrue(normalized["relevant"], "Should be true when any findings are not irrelevant")
        self.assertEqual(len(normalized["findings"]), 2)
        for f in normalized["findings"]:
            self.assertEqual(f["relevance_level"], "background")
            self.assertFalse(f["include_in_main_report"], "background should not be main report")

    def test_all_irrelevant_findings_set_relevant_false(self):
        result = {
            "findings": [
                {"relevance_level": "irrelevant", "claim_cn": "Not related at all."},
                {"relevance_level": "irrelevant", "claim_cn": "Also irrelevant."},
            ]
        }
        normalized = normalize_finding_relevance(result)
        self.assertFalse(normalized["relevant"])
        self.assertEqual(normalized["findings"], [], "Irrelevant findings should be dropped")

    def test_mixed_relevance_is_relevant_and_drops_only_irrelevant(self):
        result = {
            "findings": [
                {"relevance_level": "direct", "claim_cn": "Direct finding A.", "tags": {}},
                {"relevance_level": "irrelevant", "claim_cn": "Should be dropped."},
                {"relevance_level": "background", "claim_cn": "Background info.", "tags": {}},
                {"relevance_level": "indirect", "claim_cn": "Indirect support.", "tags": {}},
            ]
        }
        normalized = normalize_finding_relevance(result)
        self.assertTrue(normalized["relevant"])
        self.assertEqual(len(normalized["findings"]), 3, "Only the irrelevant finding should be dropped")
        levels = {f["relevance_level"] for f in normalized["findings"]}
        self.assertEqual(levels, {"direct", "indirect", "background"})

    def test_include_in_main_report_only_true_for_direct(self):
        result = {
            "findings": [
                {"relevance_level": "direct", "claim_cn": "Direct.", "tags": {}},
                {"relevance_level": "indirect", "claim_cn": "Indirect.", "tags": {}},
                {"relevance_level": "background", "claim_cn": "Bg.", "tags": {}},
            ]
        }
        normalized = normalize_finding_relevance(result)
        for f in normalized["findings"]:
            if f["relevance_level"] == "direct":
                self.assertTrue(f["include_in_main_report"])
            else:
                self.assertFalse(f["include_in_main_report"])

    def test_finding_without_relevance_level_defaults_from_relevant_flag(self):
        result = {
            "relevant": True,
            "findings": [
                {"claim_cn": "No explicit level, assumed relevant.", "tags": {}},
            ]
        }
        normalized = normalize_finding_relevance(result)
        self.assertEqual(len(normalized["findings"]), 1)
        self.assertEqual(normalized["findings"][0]["relevance_level"], "direct")

    def test_empty_findings_list_handled_gracefully(self):
        result = {"findings": []}
        normalized = normalize_finding_relevance(result)
        self.assertEqual(normalized["findings"], [])
        self.assertFalse(normalized["relevant"])

    def test_results_without_findings_key_defaults_to_empty(self):
        result = {}
        normalized = normalize_finding_relevance(result)
        self.assertEqual(normalized["findings"], [])
        self.assertFalse(normalized["relevant"])


class TestNormalizeFindingSchema(unittest.TestCase):
    """Tests for _normalize_finding_schema - field normalization."""

    def test_relation_direction_falls_back_to_not_applicable(self):
        finding = _normalize_finding_schema({
            "relation": {"subject": "X", "predicate": "Y", "object": "Z", "direction": ""},
        })
        self.assertEqual(finding["relation"]["direction"], "not_applicable")

    def test_relation_direction_invalid_value_replaced(self):
        finding = _normalize_finding_schema({
            "relation": {"subject": "A", "predicate": "increases", "direction": "garbage"},
        })
        self.assertEqual(finding["relation"]["direction"], "not_applicable")

    def test_relation_without_dict_defaults_to_empty(self):
        finding = _normalize_finding_schema({"relation": "not_a_dict"})
        for key in ("subject", "predicate", "object", "qualifier", "direction"):
            self.assertIn(key, finding["relation"])
        self.assertEqual(finding["relation"]["direction"], "not_applicable")

    def test_variables_list_normalized_with_roles(self):
        finding = _normalize_finding_schema({
            "variables": [
                {"name": "sleep", "role": "exposure"},
                {"name": "memory", "role": "outcome"},
                "bare_string",
                42,
            ],
        })
        vars_list = finding["variables"]
        # _stringify_metadata_value converts bare_string -> "bare_string" and 42 -> "42",
        # so all four inputs produce valid entries with role="unknown".
        self.assertEqual(len(vars_list), 4)
        names_roles = {(v["name"], v["role"]) for v in vars_list}
        self.assertIn(("sleep", "exposure"), names_roles)
        self.assertIn(("memory", "outcome"), names_roles)

    def test_variables_default_role_is_unknown(self):
        finding = _normalize_finding_schema({
            "variables": [{"name": "some_var"}],
        })
        self.assertEqual(finding["variables"][0]["role"], "unknown")

    def test_topics_from_legacy_tags_preserved(self):
        finding = _normalize_finding_schema({
            "tags": {"domain": "neuroscience", "method": "EEG"},
        })
        self.assertEqual(finding["topic_tags"], {"domain": "neuroscience", "method": "EEG"})
        self.assertEqual(finding["tags"], {"domain": "neuroscience", "method": "EEG"})

    def test_topic_tags_overrides_legacy_tags(self):
        finding = _normalize_finding_schema({
            "topic_tags": {"key1": "val1"},
            "tags": {"key2": "val2"},
        })
        self.assertEqual(finding["topic_tags"], {"key1": "val1"})
        self.assertEqual(finding["tags"], {"key1": "val1"})

    def test_constraints_list_stringified(self):
        finding = _normalize_finding_schema({
            "constraints": ["only humans", "sample size > 20", None, ""],
        })
        self.assertEqual(finding["constraints"], ["only humans", "sample size > 20"])

    def test_context_normalizes_all_fields(self):
        finding = _normalize_finding_schema({
            "context": {
                "study_type": "RCT",
                "sample_or_system": "adult humans",
                "condition": "sleep deprivation",
                "method": "polysomnography",
            },
        })
        ctx = finding["context"]
        self.assertEqual(ctx["study_type"], "RCT")
        self.assertEqual(ctx["sample_or_system"], "adult humans")
        self.assertEqual(ctx["condition"], "sleep deprivation")
        self.assertEqual(ctx["method"], "polysomnography")


class TestCosineSimilarity(unittest.TestCase):
    """Tests for cosine_similarity, including edge cases."""

    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0)

    def test_orthogonal_vectors_return_zero(self):
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v1, v2), 0.0)

    def test_opposite_vectors_return_negative_one(self):
        v1 = [1.0, 0.0]
        v2 = [-1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v1, v2), -1.0)

    def test_zero_magnitude_vector_returns_zero(self):
        v_zero = [0.0, 0.0, 0.0]
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine_similarity(v_zero, v), 0.0)
        self.assertAlmostEqual(cosine_similarity(v, v_zero), 0.0)

    def test_single_element_vectors(self):
        self.assertAlmostEqual(cosine_similarity([5.0], [5.0]), 1.0)
        self.assertAlmostEqual(cosine_similarity([5.0], [-5.0]), -1.0)

    def test_high_dimensional_vectors(self):
        v1 = [float(i) for i in range(100)]
        v2 = [float(i * 2) for i in range(100)]
        result = cosine_similarity(v1, v2)
        self.assertAlmostEqual(result, 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
