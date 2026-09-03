from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_export_paths import (
    artifact_export_directory,
    direction_export_directory,
    export_direction_label,
)


class ExportPathTests(unittest.TestCase):
    def test_direction_labels_are_shared_by_every_export(self):
        self.assertEqual(export_direction_label("purchase"), "Mua vào")
        self.assertEqual(export_direction_label("sold"), "Bán ra")
        self.assertEqual(export_direction_label(("purchase", "sold")), "Mua vào & Bán ra")
        self.assertEqual(export_direction_label("all"), "Mua vào & Bán ra")

    def test_result_files_go_directly_inside_the_direction_directory(self):
        self.assertEqual(
            direction_export_directory(Path("D:/Exports"), "0101234567", "purchase"),
            Path("D:/Exports/0101234567/Mua vào"),
        )

    def test_artifacts_get_a_kind_and_date_subdirectory(self):
        self.assertEqual(
            artifact_export_directory(
                Path("D:/Exports"), "0101234567", ("purchase", "sold"),
                "html", "2026-01-01", "2026-09-03",
            ),
            Path("D:/Exports/0101234567/Mua vào & Bán ra/HTML 2026-01-01_2026-09-03"),
        )


if __name__ == "__main__":
    unittest.main()
