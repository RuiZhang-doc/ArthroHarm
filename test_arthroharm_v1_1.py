#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("arthroharm_v11", HERE / "arthroharm_extract_v1_1.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)
RULES = MODULE.load_rules(HERE / "arthroharm_rules_v1.1.json")


class ArthroHarmV11Tests(unittest.TestCase):
    def test_word_boundaries_block_known_substring_false_positives(self):
        self.assertEqual(MODULE.category_hits("Patients were studied prospectively.", RULES), [])
        self.assertEqual(MODULE.category_hits("The paper was prepared by Lee et al.", RULES), [])
        self.assertTrue(MODULE.category_hits("One patient died after surgery.", RULES))
        self.assertTrue(MODULE.category_hits("Pulmonary embolism occurred in one patient.", RULES))

    def test_methods_are_excluded_and_results_are_allowed(self):
        sentence = "Adverse events will be recorded during follow-up."
        self.assertFalse(MODULE.body_context_allowed("Methods > Outcomes", sentence, RULES))
        sentence = "Two patients developed nausea during the first 24 hours."
        self.assertTrue(MODULE.body_context_allowed("Results > Adverse events", sentence, RULES))

    def test_clause_numeric_binding(self):
        sentence = "Two patients developed nausea, whereas pain scores improved in 40 patients."
        start = sentence.lower().index("nausea")
        self.assertEqual(MODULE.parse_body_numeric(sentence, start, RULES)[0], 2)

    def test_multirow_table_header_binds_arm_denominator_and_time(self):
        xml = """<article><body><sec><title>Results</title><table-wrap><label>Table 1</label>
        <caption><p>Adverse events</p></caption><table><thead>
        <tr><th rowspan='2'>Event</th><th colspan='2'>Morphine group (n=20)</th><th colspan='2'>Block group (n=19)</th></tr>
        <tr><th>PACU</th><th>24 h</th><th>PACU</th><th>24 h</th></tr></thead><tbody>
        <tr><td>Nausea</td><td>5</td><td>6</td><td>-</td><td>-</td></tr>
        </tbody></table></table-wrap></sec></body></article>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "AH-P99_test.nxml"
            path.write_text(xml, encoding="utf-8")
            rows = MODULE.extract_file(path, RULES)
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["Numerator"] for row in rows], [5, 6, 0, 0])
        self.assertEqual([row["Denominator"] for row in rows], [20, 20, 19, 19])
        self.assertEqual([row["Time Window"] for row in rows], ["PACU", "24 h", "PACU", "24 h"])
        self.assertTrue(all(row["Poolability"] == "Directly poolable" for row in rows))

    def test_unknown_row_in_safety_table_is_retained_without_guessing_category(self):
        xml = """<article><body><sec><title>Results</title><table-wrap><caption><p>Adverse events</p></caption>
        <table><thead><tr><th>Event</th><th>Control (n=10)</th></tr></thead><tbody>
        <tr><td>Unexpected symptom</td><td>2</td></tr></tbody></table></table-wrap></sec></body></article>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "AH-P98_test.nxml"; path.write_text(xml, encoding="utf-8")
            rows = MODULE.extract_file(path, RULES)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Predicted Category"], "Unmappable")
        self.assertEqual(rows[0]["Numerator"], 2)
        self.assertEqual(rows[0]["Denominator"], 10)

    def test_caption_event_with_continuous_values_does_not_invent_counts(self):
        xml = """<article><body><sec><title>Results</title><table-wrap><caption><p>Area of numbness</p></caption>
        <table><thead><tr><th>Time</th><th>M group (n=31)</th></tr></thead><tbody>
        <tr><td>2 weeks postoperative</td><td>24.42 ± 3.52</td></tr></tbody></table></table-wrap></sec></body></article>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "AH-P97_test.nxml"; path.write_text(xml, encoding="utf-8")
            rows = MODULE.extract_file(path, RULES)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Numerator"], "")
        self.assertEqual(rows[0]["Denominator"], 31)
        self.assertEqual(rows[0]["Time Window"], "2 weeks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
