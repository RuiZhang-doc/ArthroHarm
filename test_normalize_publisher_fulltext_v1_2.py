#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("adapter", HERE / "normalize_publisher_fulltext_v1_2.py")
A = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(A)


class AdapterTests(unittest.TestCase):
    def test_sections_and_linked_table_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            main = folder / "main.html"
            main.write_text('''<html><body><h1 data-test="article-title">Trial title</h1>
            <section data-title="Results"><div><h2>Results</h2><p>Two infections occurred.</p>
            <figure><a href="/article/x/tables/1">Full size table</a></figure></div></section></body></html>''')
            table = folder / "table_01.html"
            table.write_text('''<html><body><figcaption>Table 1 Complications</figcaption><table>
            <tr><th>Event</th><th>Control (n=20)</th></tr><tr><td>Infection</td><td>2</td></tr></table></body></html>''')
            root, diagnostic = A.normalized_article(main, [table])
            self.assertEqual(root.xpath("string(.//article-title)"), "Trial title")
            self.assertEqual(root.xpath("string(.//sec/title)"), "Results")
            self.assertEqual(root.xpath("string(.//table-wrap/caption/p)"), "Table 1 Complications")
            self.assertEqual(root.xpath("string(.//table//td[2])"), "2")
            self.assertEqual(diagnostic["table_count"], 1)

    def test_nested_recommendation_section_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "main.html"
            path.write_text('''<html><body><h1 data-test="article-title">Trial</h1><section data-title="Results">
            <p>Valid result.</p><section data-title="Inline Recommendations"><p>Noise.</p></section></section></body></html>''')
            root, _ = A.normalized_article(path, [])
            self.assertIn("Valid result", " ".join(root.itertext()))
            self.assertNotIn("Noise", " ".join(root.itertext()))


if __name__ == "__main__": unittest.main(verbosity=2)
