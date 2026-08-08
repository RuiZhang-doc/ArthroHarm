#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("arthroharm_v12", HERE / "arthroharm_extract_v1_2.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)
RULES = MODULE.load_rules(HERE / "arthroharm_rules_v1.2.json")


def extract(xml: str):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "AH-P99_test.nxml"
        path.write_text(xml, encoding="utf-8")
        return MODULE.extract_file(path, RULES)


class ArthroHarmV12Tests(unittest.TestCase):
    def test_baseline_table_is_excluded_even_with_event_like_comorbidity(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Baseline demographic characteristics</p></caption>
        <table><thead><tr><th>Variable</th><th>Control (n=20)</th></tr></thead><tbody>
        <tr><td>Hypertension</td><td>10 (50%)</td></tr></tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(rows, [])

    def test_mixed_table_only_opens_unknown_rows_under_safety_subheading(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Functional outcomes and postoperative complications</p></caption>
        <table><thead><tr><th>End point</th><th>Control (n=20)</th><th>P value</th></tr></thead><tbody>
        <tr><td>Quadriceps strength</td><td>4.2 ± 1.1</td><td>0.4</td></tr>
        <tr><td>Postoperative complications</td><td>Postoperative complications</td><td>Postoperative complications</td></tr>
        <tr><td>Unexpected symptom</td><td>2</td><td>0.2</td></tr>
        </tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Raw Event Term"], "Unexpected symptom")
        self.assertEqual(rows[0]["Numerator"], 2)

    def test_p_value_column_is_never_emitted(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Postoperative complications</p></caption>
        <table><thead><tr><th>Event</th><th>Control (n=20)</th><th>P value</th></tr></thead><tbody>
        <tr><td>Deep vein thrombosis</td><td>2</td><td>0.037</td></tr></tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Arm"], "Control")
        self.assertEqual(rows[0]["Numerator"], 2)

    def test_complement_count_uses_header_denominator(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Complications</p></caption>
        <table><thead><tr><th>Event</th><th>Group 1 (n=56)</th></tr></thead><tbody>
        <tr><td>Deep vein thrombosis, n(%)</td><td>28:28</td></tr></tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["Numerator"], rows[0]["Denominator"]), (28, 56))
        self.assertIn("TABLE_COUNT_COMPLEMENT", rows[0]["Rule ID"])

    def test_group_size_summary_column_supplies_denominators(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Functional outcomes and postoperative complications</p></caption>
        <table><thead><tr><th>End point</th><th>1:2:3</th><th>1-FNB</th><th>2-FTB</th><th>3-ACB</th><th>P</th></tr></thead><tbody>
        <tr><td>Postoperative Complications (%)</td><td>Postoperative Complications (%)</td><td>Postoperative Complications (%)</td><td>Postoperative Complications (%)</td><td>Postoperative Complications (%)</td><td>Postoperative Complications (%)</td></tr>
        <tr><td>Catheter Dislodgment</td><td>34:32:33</td><td>3 (8.82)</td><td>1 (3.13)</td><td>0 (0)</td><td>0.177</td></tr>
        </tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["Numerator"] for r in rows], [3, 1, 0])
        self.assertEqual([r["Denominator"] for r in rows], [34, 32, 33])

    def test_explicit_zero_propagates_across_event_list(self):
        rows = extract("""<article><body><sec><title>Results</title><p>No skin ulcer, hematoma, infection, or liver injury was observed.</p></sec></body></article>""")
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(all(r["Numerator"] == 0 for r in rows))

    def test_body_term_without_count_or_occurrence_is_suppressed(self):
        rows = extract("""<article><body><sec><title>Results</title><p>Other outcomes included stiffness and patient satisfaction.</p></sec></body></article>""")
        self.assertEqual(rows, [])

    def test_either_group_is_canonical_overall(self):
        rows = extract("""<article><body><sec><title>Results</title><p>No deep infection occurred in either group.</p></sec></body></article>""")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Arm"], "Overall")

    def test_caption_time_is_inherited_by_safety_rows(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Complications within 2 weeks</p></caption>
        <table><thead><tr><th>Event</th><th>Control (n=20)</th></tr></thead><tbody>
        <tr><td>Deep vein thrombosis</td><td>1</td></tr></tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(rows[0]["Time Window"], "within 2 weeks")

    def test_transposed_safety_table_extracts_event_columns_not_p_values(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Complications of three groups</p></caption>
        <table><thead><tr><th>Group</th><th>Number</th><th>Effusion</th><th>Superficial infection</th></tr></thead><tbody>
        <tr><td>A</td><td>193</td><td>7 (3.6%)</td><td>19 (9.8%)</td></tr>
        <tr><td>B</td><td>195</td><td>2 (1.0%)</td><td>6 (3.1%)</td></tr>
        <tr><td>P</td><td></td><td>0.163</td><td>0.008</td></tr>
        </tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(len(rows), 4)
        self.assertEqual([(r["Arm"], r["Numerator"], r["Denominator"]) for r in rows], [("Group A", 7, 193), ("Group A", 19, 193), ("Group B", 2, 195), ("Group B", 6, 195)])

    def test_none_suffered_from_propagates_zero_across_list(self):
        rows = extract("""<article><body><sec><title>Results</title><p>None of the patients in either group suffered from deep vein thrombosis, pulmonary embolism, or myocardial infarction.</p></sec></body></article>""")
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(all(r["Numerator"] == 0 for r in rows))
        self.assertTrue(all(r["Arm"] == "Overall" for r in rows))

    def test_bare_parenthetical_group_size_is_denominator(self):
        arm, denominator, _ = MODULE.parse_header_arm_denominator("Group B (100)", RULES)
        self.assertEqual((arm, denominator), ("Group B", 100))

    def test_poolability_does_not_require_time_when_arm_count_and_denominator_exist(self):
        rows = extract("""<article><body><sec><title>Results</title><table-wrap><caption><p>Complications</p></caption>
        <table><thead><tr><th>Event</th><th>Control (n=20)</th></tr></thead><tbody><tr><td>Deep vein thrombosis</td><td>2</td></tr></tbody></table></table-wrap></sec></body></article>""")
        self.assertEqual(rows[0]["Poolability"], "Directly poolable")

    def test_numerator_without_denominator_is_transformable(self):
        rows = extract("""<article><body><sec><title>Results</title><p>Two patients developed nausea.</p></sec></body></article>""")
        self.assertEqual(rows[0]["Poolability"], "Transformable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
