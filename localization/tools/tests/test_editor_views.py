"""Verify browser-facing XML text and table boundaries independently of the UI."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editor_server import formatted_primary_text, matches_filters, page_slice, primary_text
from lekmod_localization.common import CatalogError


class EditorViewTests(unittest.TestCase):
    def test_multiline_xml_indentation_is_not_editor_content(self):
        raw = "\n\t\t\t[COLOR_POSITIVE_TEXT]LEKMOD v35.3[ENDCOLOR]\n\t\t"
        self.assertEqual(primary_text(raw), "[COLOR_POSITIVE_TEXT]LEKMOD v35.3[ENDCOLOR]")
        replaced = formatted_primary_text(raw, "New <leader> & [ICON_CULTURE]")
        self.assertEqual(primary_text(replaced), "New <leader> & [ICON_CULTURE]")
        self.assertTrue(replaced.startswith("\n\t\t\tNew &lt;leader&gt; &amp;"))
        self.assertTrue(replaced.endswith("\n\t\t"))
        self.assertEqual(primary_text(formatted_primary_text(raw, "First\nSecond")),
                         "First\nSecond")

    def test_filters_and_paging_use_selected_rows_and_inclusive_dates(self):
        row = {"classification": "vanilla_modified", "translation_status": "stale",
               "english_edited_at": "2026-09-27T12:00:00+00:00",
               "translation_updated_at": "2026-09-28T09:00:00+00:00"}
        self.assertTrue(matches_filters(row, {"kind": "vanilla_modified", "status": "stale",
                                              "date_field": "translation_updated_at",
                                              "date_from": "2026-09-28",
                                              "date_to": "2026-09-28"}, primary=False))
        self.assertFalse(matches_filters(row, {"date_from": "2026-09-28"}, primary=False))
        self.assertFalse(matches_filters({"kind": "Replace"},
                                         {"kind": "Row"}, primary=True))
        entries = [{"key": str(index)} for index in range(150)]
        self.assertEqual(len(page_slice(entries, 0, "100")), 100)
        self.assertEqual(len(page_slice(entries, 100, "100")), 50)
        self.assertEqual(page_slice(entries, 0, "all"), entries)
        with self.assertRaises(CatalogError):
            page_slice(entries, -1, "50")


if __name__ == "__main__":
    unittest.main()
