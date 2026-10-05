"""Verify browser-facing XML text and table boundaries independently of the UI."""

import sys
from pathlib import Path
import io
import json
from hashlib import sha256
from http.server import HTTPServer
import threading
import time
from urllib.request import Request, urlopen
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editor_server import (Editor, make_handler, formatted_primary_text, matches_filters,
                           page_slice, primary_text, primary_creation_info,
                           require_fresh_translation_files, safe_ui_event_detail)
from lekmod_localization.common import CatalogError


class EditorViewTests(unittest.TestCase):
    def test_project_switch_records_upgrade_even_when_translation_copy_is_off(self):
        """Release comparisons are required independently of optional saved-row transfer."""
        with tempfile.TemporaryDirectory() as directory:
            old, new = Path(directory) / 'old', Path(directory) / 'new'
            old.mkdir(); new.mkdir()
            editor = object.__new__(Editor)
            editor.ready = True
            with patch('editor_server.REPO_ROOT', old), \
                 patch('editor_server.validate_project'), \
                 patch('editor_server.save_settings', side_effect=lambda value: value):
                result = editor.connect({'project_path': str(new), 'game_path': '',
                                         'carry_translations': False})
            self.assertTrue(result['restart'])
            pending = json.loads((new / 'localization/workspace/pending-transfer.json').read_text())
            self.assertEqual(pending, {'from': str(old), 'carry_translations': False})

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

    def test_local_date_filter_compares_instants_and_keeps_dst_boundaries(self):
        """Prague's March 29 is a 23-hour local day; sender offsets are irrelevant."""
        bounds = {'date_field': 'translation_updated_at',
                  'date_from': '2026-03-29', 'date_to': '2026-03-29',
                  'date_start_utc': '2026-03-28T23:00:00Z',
                  'date_end_utc': '2026-03-29T22:00:00Z'}
        for instant in ('2026-03-28T23:00:00Z', '2026-03-28T20:00:00-03:00',
                        '2026-03-29T21:59:59.999999+00:00'):
            self.assertTrue(matches_filters({'translation_updated_at': instant}, bounds, primary=False))
        for instant in ('2026-03-28T22:59:59Z', '2026-03-29T22:00:00Z', ''):
            self.assertFalse(matches_filters({'translation_updated_at': instant}, bounds, primary=False))
        with self.assertRaisesRegex(CatalogError, 'invalid local date boundary'):
            matches_filters({'translation_updated_at': '2026-03-29T13:10:00-03:00'},
                            {**bounds, 'date_start_utc': '2026-03-29T00:00:00'}, primary=False)

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
            source = root / 'localization/en_US/primary.xml'
            source.parent.mkdir(parents=True)
            reference = root / "fingerprints.json.gz"
            source.write_text('<GameData>\n\t<Language_en_US>\n\t\t<Row Tag="TXT_KEY_ONE">\n'
                              '\t\t\t<Text>edited English</Text>\n\t\t</Row>\n'
                              '\t</Language_en_US>\n</GameData>', encoding="utf-8")
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

    def test_translation_handoff_contains_full_csv_and_merge_instructions(self):
        """Exports pin the exact CSV, but tell maintainers to merge its rows."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "translations"
            approved.mkdir()
            row = ("key,source_fingerprint,text,gender,plurality,translator_note,updated_at\n"
                   "TXT_KEY_ONE," + "a" * 64 + ",Перевод,,,,2026-09-28T13:48:15Z\n")
            (approved / "RU_RU.csv").write_text(row, encoding="utf-8-sig")
            source = root / "primary.xml"
            source.write_text("English source", encoding="utf-8")
            reference = root / "reference.json.gz"
            reference.write_bytes(b"reference fingerprints")
            editor = object.__new__(Editor)
            editor.ready = True
            with patch("editor_server.REPO_ROOT", root), \
                 patch("editor_server.TRANSLATIONS", approved), \
                 patch("editor_server.sync_primary_english.DEFAULT_ENGLISH", source), \
                 patch("editor_server.manage.DEFAULT_REFERENCE", reference), \
                 patch.object(Editor, "manifest", return_value={"locales": {"RU_RU": {}}}):
                content = editor.export_locale("RU_RU")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                packaged = archive.read("translations/RU_RU.csv")
                manifest = json.loads(archive.read("manifest.json"))
                instructions = archive.read("README.txt").decode("utf-8")
                self.assertEqual(packaged, (approved / "RU_RU.csv").read_bytes())
                self.assertEqual(manifest["locales"]["RU_RU"], sha256(packaged).hexdigest())
                self.assertEqual(manifest["english_sha256"], sha256(source.read_bytes()).hexdigest())
                self.assertIn("merge_translation_handoff.py", instructions)
                self.assertIn("Do not replace", instructions)
                self.assertNotIn("vanilla-snapshot.json.gz", archive.namelist())

    def test_export_selects_only_named_languages(self):
        """A multi-language handoff has an exact manifest and no unselected CSV."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "translations"
            approved.mkdir()
            for locale in ("RU_RU", "DE_DE", "FR_FR"):
                (approved / f"{locale}.csv").write_text(
                    "key,source_fingerprint,text,gender,plurality,translator_note\n",
                    encoding="utf-8-sig")
            source, reference = root / "primary.xml", root / "reference.json.gz"
            source.write_text("English", encoding="utf-8")
            reference.write_bytes(b"reference")
            editor = object.__new__(Editor)
            editor.ready = True
            with patch("editor_server.REPO_ROOT", root), \
                 patch("editor_server.TRANSLATIONS", approved), \
                 patch("editor_server.sync_primary_english.DEFAULT_ENGLISH", source), \
                 patch("editor_server.manage.DEFAULT_REFERENCE", reference), \
                 patch.object(Editor, "manifest", return_value={
                     "locales": {locale: {} for locale in ("RU_RU", "DE_DE", "FR_FR")}}):
                content = editor.export_locales(["RU_RU", "DE_DE"])
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "translations/RU_RU.csv", "translations/DE_DE.csv",
                    "manifest.json", "README.txt"})
                metadata = json.loads(archive.read("manifest.json"))
                self.assertEqual(set(metadata["locales"]), {"RU_RU", "DE_DE"})
                with self.assertRaisesRegex(CatalogError, "select at least one"):
                    with patch.object(Editor, "manifest", return_value={"locales": {}}):
                        editor.export_locales([])

    def test_background_save_serializes_jobs_and_reports_result(self):
        """A slow rebuild leaves the HTTP loop free and rejects a second save."""
        editor = object.__new__(Editor)
        editor.save_state = {"state": "idle"}
        started, release = threading.Event(), threading.Event()

        def slow_save(data):
            started.set()
            self.assertTrue(release.wait(2))
            return {"approved_sha256": "b" * 64, "undo_available": True,
                    "redo_available": False}

        with patch.object(Editor, "save_translation", side_effect=slow_save), \
             patch.object(Editor, "record_event"):
            first = editor.start_translation_save({"locale": "RU_RU", "key": "TXT_KEY_ONE"})
            self.assertTrue(started.wait(1))
            with self.assertRaisesRegex(CatalogError, "previous row"):
                editor.start_translation_save({"locale": "RU_RU", "key": "TXT_KEY_TWO"})
            release.set()
            for _ in range(100):
                if editor.save_state["state"] != "running":
                    break
                time.sleep(.01)
        self.assertEqual(editor.save_state["id"], first["id"])
        self.assertEqual(editor.save_state["approved_sha256"], "b" * 64)

    def test_binary_handoff_preview_and_reviewed_apply_use_same_package(self):
        """The localhost API carries the ZIP as bytes, then checks its review ID."""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            game = project / "game.xml"
            game.write_text("<GameData />", encoding="utf-8")
            editor = object.__new__(Editor)
            editor.ready = True
            editor.save_state = {"state": "idle"}
            editor.handoff_data = None
            editor.handoff_preview = None
            editor.handoff_id = ""
            editor.handoff_packages = []
            editor.log_lock = threading.Lock()
            editor.log_path = project / 'workspace/actions.jsonl'
            editor.events = []
            editor.snapshot = None
            editor.actions = []
            editor.cursor = 0
            preview = {"locales": ["RU_RU"], "items": [{
                "id": "RU_RU:TXT_KEY_A", "status": "conflict", "choice": "review"}],
                "pending": ["RU_RU:TXT_KEY_A"], "target_sha256": {"RU_RU": "a" * 64},
                "applied": False}
            applied = {**preview, "pending": [], "items": [], "backups": {}}
            server = HTTPServer(("127.0.0.1", 0), make_handler(editor, "test-token", 0))
            server.RequestHandlerClass = make_handler(editor, "test-token", server.server_port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            headers = {"Origin": base, "X-Editor-Token": "test-token"}
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('README.txt', 'translation ZIP')
            payload_bytes = stream.getvalue()
            try:
                with patch("editor_server.review_merge", side_effect=[preview, applied]) as merge, \
                     patch("editor_server.WORKSPACE", project / 'workspace'), \
                     patch("editor_server.build_shipped_localization.DEFAULT_SOURCE", game), \
                     patch.object(Editor, "record_event"):
                    with urlopen(base + "/api/save-status", timeout=5) as response:
                        self.assertEqual(json.load(response)["state"], "idle")
                    with urlopen(Request(base + "/api/handoff-preview", data=payload_bytes,
                                         headers=headers), timeout=5) as response:
                        review = json.load(response)
                    self.assertEqual(review["locales"], ["RU_RU"])
                    self.assertTrue(review["handoff_id"])
                    self.assertEqual(editor.handoff_data, payload_bytes)
                    payload = json.dumps({"handoff_id": review["handoff_id"],
                                          "choices": {"RU_RU:TXT_KEY_A": "keep"}}).encode()
                    with urlopen(Request(base + "/api/handoff-apply", data=payload,
                                         headers={**headers, "Content-Type": "application/json"}),
                                 timeout=5) as response:
                        result = json.load(response)
                    self.assertFalse(result["applied"])
                    self.assertEqual(merge.call_args.kwargs["expected"], {"RU_RU": "a" * 64})
                    self.assertIsNone(editor.handoff_data)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_imported_approval_metadata_overrides_old_editor_draft(self):
        """A merged CSV immediately supplies the new note and grammar in the table."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "translations"
            approved.mkdir()
            (approved / "RU_RU.csv").write_text(
                "key,source_fingerprint,text,gender,plurality,translator_note,updated_at\n"
                "TXT_KEY_ONE," + "a" * 64 +
                ",Новый перевод,feminine,2,Imported note,2026-09-29T10:00:00Z\n",
                encoding="utf-8-sig")
            primary = root / "primary.xml"
            primary.write_text("English source", encoding="utf-8")
            row = {"key": "TXT_KEY_ONE", "source_fingerprint": "a" * 64,
                   "lekmod_en_US": "English", "vanilla_en_US": "", "vanilla_target": "",
                   "translation": "Старый черновик", "translator_note": "Old note",
                   "translation_gender": "", "translation_plurality": ""}
            editor = object.__new__(Editor)
            editor.english_dates = {}
            with patch.object(Editor, "path", return_value=root / "generated.csv"), \
                 patch("editor_server.csv_rows", return_value=[row]), \
                 patch("editor_server.TRANSLATIONS", approved), \
                 patch("editor_server.sync_primary_english.DEFAULT_ENGLISH", primary), \
                 patch("editor_server.manage.read_config",
                       return_value={"build": {"shipped": True}}):
                loaded = editor.rows("RU_RU", "buildings", "", 0)["rows"][0]
            self.assertEqual(loaded["translation"], "Новый перевод")
            self.assertEqual(loaded["translation_gender"], "feminine")
            self.assertEqual(loaded["translation_plurality"], "2")
            self.assertEqual(loaded["translator_note"], "Imported note")

    def test_failed_rebuild_after_merge_restores_csv_and_xml(self):
        """A failed generated-XML rebuild cannot leave imported CSVs half applied."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translations = root / "translations"
            translations.mkdir()
            target = translations / "RU_RU.csv"
            target.write_bytes(b"team rows")
            backup = root / "backup.csv"
            backup.write_bytes(target.read_bytes())
            game = root / "game.xml"
            game.write_bytes(b"old game XML")
            editor = object.__new__(Editor)
            editor.handoff_data = b"archive"
            editor.handoff_preview = {"target_sha256": {"RU_RU": "a" * 64}}
            editor.handoff_id = "session-1"
            editor.handoff_packages = [b'archive']
            editor.snapshot = None
            editor.actions = []
            editor.cursor = 0

            def merged(*args, **kwargs):
                target.write_bytes(b"incoming rows")
                game.write_bytes(b"new game XML")
                return {"applied": True, "backups": {"translations/RU_RU.csv": str(backup),
                         "game.xml": str(root / 'original-game.xml')}}

            (root / 'original-game.xml').write_bytes(game.read_bytes())
            with patch("editor_server.review_merge", side_effect=merged), \
                 patch("editor_server.REPO_ROOT", root), \
                 patch("editor_server.TRANSLATIONS", translations), \
                 patch("editor_server.build_shipped_localization.DEFAULT_SOURCE", game), \
                 patch("editor_server.manage.read_config", return_value={}), \
                 patch("editor_server.manage.prepare",
                       side_effect=[CatalogError("broken rebuild"), None]) as prepare:
                with self.assertRaisesRegex(CatalogError, "broken rebuild"):
                    editor.apply_handoff({"handoff_id": "session-1", "choices": {}})
            self.assertEqual(prepare.call_count, 2)
            self.assertEqual(target.read_bytes(), b"team rows")
            self.assertEqual(game.read_bytes(), b"old game XML")


if __name__ == "__main__":
    unittest.main()
