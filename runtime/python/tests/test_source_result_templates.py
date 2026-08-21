import unittest

from mia_source_results import (
    _detail_template_schema,
    _overview_template_schema,
)


class SourceResultTemplateTests(unittest.TestCase):
    def test_electronic_overview_schema_comes_from_source_template(self):
        schema = _overview_template_schema("electronic", "purchase")
        self.assertEqual(schema[0][0], "stt")
        self.assertEqual(schema[0][1].strip().upper(), "STT")
        self.assertEqual(len(schema), 19)
        self.assertEqual(
            [key for key, _ in schema[:5]],
            ["stt", "khmshdon", "khhdon", "shdon", "tdlap"],
        )
        self.assertTrue(all(title.strip() for _, title in schema))

    def test_cash_register_direction_templates_keep_exact_source_shape(self):
        purchase = _overview_template_schema("cash_register", "purchase")
        sold = _overview_template_schema("cash_register", "sold")
        self.assertEqual(len(purchase), 17)
        self.assertEqual(len(sold), 17)
        self.assertEqual(purchase[0][1].strip().upper(), "STT")
        self.assertEqual(sold[0][1].strip().upper(), "STT")
        self.assertIn("nbdchi", [key for key, _ in purchase])
        self.assertIn("nmdchi", [key for key, _ in sold])

    def test_detail_schema_matches_the_prepared_source_export_workbook(self):
        schema = _detail_template_schema()
        self.assertEqual(len(schema), 38)
        self.assertEqual(schema[0][0], "stt")
        self.assertEqual(schema[0][1].strip().upper(), "STT")
        self.assertEqual(schema[1][1].strip(), "Mẫu số HD")
        self.assertTrue(all(title.strip() for _, title in schema))
        self.assertFalse(any(key.startswith("raw_") or key.endswith("_path") for key, _ in schema))


if __name__ == "__main__":
    unittest.main()
