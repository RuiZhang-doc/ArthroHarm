#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("pdf_adapter", HERE / "normalize_publisher_pdf_v1_2.py")
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


class PDFAdapterTests(unittest.TestCase):
    def test_xml_forbidden_control_characters_are_removed(self):
        self.assertEqual(A.clean("A\x00B\x0cC\ufffeD"), "ABCD")

    def test_numbered_and_spaced_display_headings(self):
        self.assertEqual(A.heading_name("2 | Materials and Methods"), "Methods")
        self.assertEqual(A.heading_name("M AT E R I A L S A N D M E T H O D S"), "Methods")
        self.assertEqual(A.heading_name("Results and analysis"), "Results")
        self.assertEqual(A.heading_name("| Results"), "Results")
        self.assertEqual(A.table_label("TA B L E 1"), "TA B L E 1")

    def test_fixed_width_table_is_preserved(self):
        layout = """Table 2
Neutral outcome summary.

Variable                         Control group        Treatment group       P value
                                 (n = 20)             (n = 20)
Measure A                        4 (20)                3 (15)                .70
Measure B                        0 (0)                 1 (5)                 1.00


Body text continues.
"""
        tables = A.parse_layout_tables(layout)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["label"], "Table 2")
        self.assertIn("Neutral outcome", tables[0]["caption"])
        self.assertEqual(tables[0]["rows"][-1][0], "Measure B")
        self.assertIn("1 (5)", tables[0]["rows"][-1])

    def test_sections_and_table_create_minimal_jats(self):
        plain = """Introduction
Background paragraph.

Methods
Participants were allocated.

Results
The prespecified outcome was reported.

Discussion
The findings were interpreted.
"""
        layout = """Table 1
Baseline summary.
Variable                  Group A             Group B
Age                       65                  66


"""
        root, diagnostics = A.build_article("Trial title", "10.1/test", plain, layout)
        self.assertEqual(root.xpath("string(.//article-title)"), "Trial title")
        self.assertEqual(root.xpath("string(.//sec[title='Results']/p)"), "The prespecified outcome was reported.")
        self.assertEqual(root.xpath("string(.//table-wrap/label)"), "Table 1")
        self.assertEqual(diagnostics["table_count"], 1)

    def test_parallel_body_columns_stop_table_capture(self):
        layout = """Table 1
Neutral outcome summary.
Variable                  Group A             Group B
Measure A                 4                   5
Measure B                 6                   7
This is ordinary prose in the left column with many words.  This is ordinary prose in the right column with many words.
"""
        tables = A.parse_layout_tables(layout)
        self.assertEqual(len(tables), 1)
        self.assertEqual(len(tables[0]["rows"]), 3)

    def test_false_table_reference_without_structure_is_rejected(self):
        layout = """Table 4. is discussed in the text.
This paragraph continues with no tabular structure.  Another prose column continues here.
More narrative content follows.  More narrative content follows here.
"""
        self.assertEqual(A.parse_layout_tables(layout), [])

    def test_coordinate_words_are_split_only_at_large_x_gaps(self):
        words = [
            {"text": "Age", "x0": 10, "x1": 28},
            {"text": "(years)", "x0": 31, "x1": 60},
            {"text": "69.5", "x0": 95, "x1": 115},
            {"text": "±", "x0": 119, "x1": 124},
            {"text": "8.6", "x0": 128, "x1": 143},
        ]
        self.assertEqual(A.cells_from_coordinate_line(words), ["Age (years)", "69.5 ± 8.6"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
