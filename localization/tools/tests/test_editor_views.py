"""Verify browser-facing XML text and table boundaries independently of the UI."""

import sys
from pathlib import Path
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editor_server import (Editor, formatted_primary_text, matches_filters,
                           page_slice, primary_text, primary_creation_info,
                           require_fresh_translation_files, safe_ui_event_detail)
from lekmod_localization.common import CatalogError


class EditorViewTests(unittest.TestCase):
    def test_old_private_snapshot_does_not_block_new_editor_baseline(self):
        """After a team reference update, Settings must remain available."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old = home / "old-snapshot.json.gz"
            old.write_bytes(b"old private text")
            with patch("editor_server.APP_HOME", home), \
                 patch("editor_server.manage.migrate_workspace"), \
                 patch("editor_server.validate_project", side_effect=ValueError("disconnected")), \
                 patch("editor_server.verify_snapshot_reference",
                       side_effect=CatalogError("old reference")):
                editor = Editor(snapshot=old)
            self.assertIsNone(editor.snapshot)
            self.assertTrue(editor.snapshot_error)
            self.assertEqual(old.read_bytes(), b"old private text")

    def test_multiline_xml_indentation_is_not_editor_content(self):
        raw = "\n\t\t\t[COLOR_POSITIVE_TEXT]LEKMOD v35.3[ENDCOLOR]\n\t\t"
        self.assertEqual(primary_text(raw), "[COLOR_POSITIVE_TEXT]LEKMOD v35.3[ENDCOLOR]")
        replaced = formatted_primary_text(raw, "New <leader> & [ICON_CULTURE]")
        self.assertEqual(primary_text(replaced), "New <leader> & [ICON_CULTURE]")
        self.assertTrue(replaced.startswith("\n\t\t\tNew &lt;leader&gt; &amp;"))
        self.assertTrue(replaced.endswith("\n\t\t"))
        self.assertEqual(primary_text(formatted_primary_text(raw, "First\nSecond")),
                         "First\nSecond")

    def test_creation_location_matches_new_row_and_rejects_ambiguous_source(self):
        document = "<GameData>\n\t<Language_en_US>\n\t</Language_en_US>\n</GameData>"
        self.assertEqual(primary_creation_info(document), {
            "source_file": "localization/en_US/primary.xml", "line": 3,
            "operation": "Row"})
        with self.assertRaisesRegex(CatalogError, "cannot locate"):
            primary_creation_info(document.replace("</Language_en_US>", "</Other>"))
        editor = object.__new__(Editor)
        with patch.object(Editor, "require_developer"), \
             self.assertRaisesRegex(CatalogError, "new English keys use Row"):
            editor.create_primary({"key": "TXT_KEY_NEW", "text": "New text",
                                   "operation": "Replace"})

    def test_renaming_and_text_edit_are_one_validated_source_change(self):
        """A Developer save must not rename an ID and silently drop text edits."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LEKMOD").mkdir()
            source = root / "localization/en_US/primary.xml"
            source.parent.mkdir(parents=True)
            source.write_text('<GameData>\n\t<Language_en_US>\n\t\t<Row Tag="TXT_KEY_OLD">\n'
                              '\t\t\t<Text>Before</Text>\n\t\t</Row>\n'
                              '\t</Language_en_US>\n</GameData>',
                              encoding="utf-8")
            editor = object.__new__(Editor)
            with patch("editor_server.REPO_ROOT", root), \
                 patch("editor_server.sync_primary_english.DEFAULT_ENGLISH", source), \
                 patch("editor_server.read_reference", return_value={"english": {}}), \
                 patch.object(Editor, "require_developer"), \
                 patch.object(Editor, "manifest", return_value={"locales": {}}), \
                 patch.object(Editor, "_save_structure", side_effect=lambda before, after, key: after):
                data = {"index": 0, "key": "TXT_KEY_OLD", "new_key": "TXT_KEY_NEW",
                        "old_text": "Before", "text": "After & later"}
                updated = editor.rename_primary(data)
                self.assertIn('Tag="TXT_KEY_NEW"', updated)
                self.assertIn('<Text>After &amp; later</Text>', updated)
                with self.assertRaisesRegex(CatalogError, "changed; reload"):
                    editor.rename_primary({**data, "old_text": "Outdated"})

    def test_warning_log_scrubs_links_paths_and_secrets(self):
        detail = safe_ui_event_detail(
            "Download failed at https://example.org/private?token=abc "
            "C:\\Users\\Neil\\secret.txt password=correct-horse")
        self.assertNotIn("example.org", detail)
        self.assertNotIn("Users", detail)
        self.assertNotIn("correct-horse", detail)
        self.assertIn("Download failed", detail)

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

    def test_parallel_editor_and_ide_changes_cannot_silently_overwrite(self):
        """Reject a save made against an outdated source or approval CSV."""
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            source, approved = Path(directory) / "primary.xml", Path(directory) / "RU_RU.csv"
            source.write_text("English", encoding="utf-8")
            approved.write_text("translation", encoding="utf-8")
            data = {"english_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "approved_sha256": hashlib.sha256(approved.read_bytes()).hexdigest()}
            require_fresh_translation_files(data, source, approved)
            approved.write_text("second editor", encoding="utf-8")
            with self.assertRaisesRegex(CatalogError, "changed since"):
                require_fresh_translation_files(data, source, approved)
            data["approved_sha256"] = hashlib.sha256(approved.read_bytes()).hexdigest()
            source.write_text("new balance", encoding="utf-8")
            with self.assertRaisesRegex(CatalogError, "changed since"):
                require_fresh_translation_files(data, source, approved)

    def test_portable_run_checks_names_skipped_git_gates(self):
        """A portable source must not attempt Git inventory even if Git is on PATH."""
        with tempfile.TemporaryDirectory() as directory:
            editor = object.__new__(Editor)
            config = {"checks": {"art": False, "primary": False,
                                 "english_sync": False, "shipped": False,
                                 "inventory": True, "unit_tests": True}}
            with patch.object(Editor, "require_developer"), \
                 patch("editor_server.REPO_ROOT", Path(directory)), \
                 patch("editor_server.shutil.which", return_value="available"), \
                 patch("editor_server.manage.read_config", return_value=config), \
                 patch("editor_server.subprocess.run", side_effect=AssertionError("Git called")):
                result = editor.check_project()
            self.assertIn("Skipped outside a Git checkout", result["summary"])
            self.assertIn("inventory, unit_tests", result["summary"])

    def test_developer_can_export_primary_without_private_snapshot(self):
        """Portable handoff contains the canonical source and a review manifest."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, reference = root / "primary.xml", root / "fingerprints.json.gz"
            source.write_text("<Text>edited English</Text>", encoding="utf-8")
            reference.write_bytes(b"reference")
            editor = object.__new__(Editor)
            with patch.object(Editor, "require_developer"), \
                 patch("editor_server.REPO_ROOT", root), \
                 patch("editor_server.sync_primary_english.DEFAULT_ENGLISH", source), \
                 patch("editor_server.manage.DEFAULT_REFERENCE", reference):
                content = editor.export_english()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertEqual(archive.read("localization/en_US/primary.xml"),
                                 source.read_bytes())
                self.assertIsNone(json.loads(archive.read("manifest.json"))["repository_commit"])
                self.assertNotIn("vanilla-snapshot.json.gz", archive.namelist())


if __name__ == "__main__":
    unittest.main()
