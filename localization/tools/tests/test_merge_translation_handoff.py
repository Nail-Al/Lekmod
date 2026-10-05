"""Team handoffs add independent translations without replacing newer rows."""

import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.common import CatalogError
from merge_translation_handoff import FIELDS, csv_records, merge_handoff
from sync_primary_english import BEGIN, END, marked_block


class HandoffMergeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.primary = self.project / "localization/en_US/primary.xml"
        self.primary.parent.mkdir(parents=True)
        self.primary.write_text("<Language_en_US />", encoding="utf-8")
        self.game = self.project / "LEKMOD/Override/CIV5Units_Mongol.xml"
        self.game.parent.mkdir(parents=True)
        self.game.write_text("<GameData>\n" + BEGIN +
                             "\t<Language_en_US>\n\t</Language_en_US>\n" +
                             END + "</GameData>\n", encoding="utf-8")
        self.reference = self.project / "localization/reference/vanilla-fingerprints.json.gz"
        self.reference.parent.mkdir(parents=True)
        self.reference.write_bytes(b"shared pinned reference")
        self.target = self.project / "localization/translations/RU_RU.csv"
        self.target.parent.mkdir(parents=True)
        self.existing = {"key": "TXT_KEY_EXISTING", "source_fingerprint": "a" * 64,
                         "text": "Существующий", "gender": "", "plurality": "",
                         "translator_note": "Team's note", "updated_at": "2026-09-27T10:00:00Z"}
        self.target.write_bytes(self.csv_bytes([self.existing], old_format=True))
        self.editor = self.project / "localization/workspace/editor"
        locale = self.editor / "RU_RU"
        locale.mkdir(parents=True)
        (self.editor / "manifest.json").write_text(json.dumps({
            "source_sha256": sha256(self.game.read_bytes()).hexdigest(),
            "source_english_sha256": sha256(
                marked_block(self.game.read_text(encoding="utf-8"))[2].encode()).hexdigest(),
            "primary_sha256": sha256(self.primary.read_bytes()).hexdigest(),
            "locales": {"RU_RU": {"files": {"buildings.csv": 2}}}}), encoding="utf-8")
        with (locale / "buildings.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, ["key", "source_fingerprint", "required_format_tokens"])
            writer.writeheader()
            writer.writerow({"key": "TXT_KEY_EXISTING", "source_fingerprint": "a" * 64,
                             "required_format_tokens": "{}"})
            writer.writerow({"key": "TXT_KEY_NEW", "source_fingerprint": "b" * 64,
                             "required_format_tokens": '{"[ICON_CULTURE]":1}'})
        self.new = {"key": "TXT_KEY_NEW", "source_fingerprint": "b" * 64,
                    "text": "Новый [ICON_CULTURE]", "gender": "", "plurality": "",
                    "translator_note": "Keep the icon", "updated_at": "2026-09-28T13:48:15Z"}
        self.zip = self.project / "handoff.zip"

    @staticmethod
    def csv_bytes(rows, old_format=False):
        with io.StringIO(newline="") as stream:
            fields = FIELDS[:-1] if old_format else FIELDS
            writer = csv.DictWriter(stream, fields, lineterminator="\n",
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            return stream.getvalue().encode("utf-8-sig")

    def package(self, rows, *, old_manifest=False, reference=None):
        content = self.csv_bytes(rows)
        metadata = {"locale": "RU_RU", "repository_commit": None,
                    "vanilla_reference_sha256": sha256(
                        self.reference.read_bytes() if reference is None else reference).hexdigest()}
        if not old_manifest:
            metadata["translation_csv_sha256"] = sha256(content).hexdigest()
        with zipfile.ZipFile(self.zip, "w") as package:
            package.writestr("manifest.json", json.dumps(metadata))
            package.writestr("README.txt", "handoff")
            package.writestr("translations/RU_RU.csv", content)
        return self.zip

    def test_preview_then_merge_preserves_team_row_and_private_backup(self):
        """An older six-column team file accepts an independent seven-column row."""
        archive = self.package([self.new], old_manifest=True)
        before = self.target.read_bytes()
        preview = merge_handoff(archive, self.project)
        self.assertEqual([(item["key"], item["status"]) for item in preview["items"]],
                         [("TXT_KEY_NEW", "new")])
        self.assertFalse(preview["applied"])
        self.assertEqual(self.target.read_bytes(), before)
        merged = merge_handoff(archive, self.project, apply=True)
        self.assertTrue(merged["applied"])
        self.assertEqual(Path(merged["backups"]["RU_RU"]).read_bytes(), before)
        result = csv_records(self.target.read_bytes(), "RU_RU")
        self.assertEqual(result["TXT_KEY_EXISTING"]["translator_note"], "Team's note")
        self.assertEqual(result["TXT_KEY_NEW"]["text"], "Новый [ICON_CULTURE]")
        self.assertEqual(result["TXT_KEY_NEW"]["updated_at"], "2026-09-28T13:48:15Z")
        again = merge_handoff(archive, self.project, apply=True)
        self.assertEqual(again["identical_count"], 1)
        self.assertEqual(again["items"], [])
        self.assertFalse(again["applied"])

    def test_reviewed_reference_extension_accepts_an_old_handoff_without_overwrite(self):
        """Adding vanilla language tables must not discard an earlier translator ZIP."""
        old = b"previous reviewed baseline"
        archive = self.package([self.new], reference=old)
        original = self.target.read_bytes()
        migration = {(sha256(old).hexdigest(), sha256(self.reference.read_bytes()).hexdigest())}
        with patch("lekmod_localization.vanilla_reference.COMPATIBLE_REFERENCE_EXTENSIONS", migration):
            preview = merge_handoff(archive, self.project)
            self.assertEqual(preview["items"][0]["status"], "new")
            self.assertEqual(self.target.read_bytes(), original)
            merged = merge_handoff(archive, self.project, apply=True)
        self.assertTrue(merged["applied"])
        records = csv_records(self.target.read_bytes(), "RU_RU")
        self.assertEqual(records[self.existing["key"]], {**self.existing, "updated_at": ""})
        self.assertEqual(records[self.new["key"]], self.new)

    def test_conflicting_key_blocks_entire_merge_and_keeps_every_byte(self):
        """Another translator's revision must not overwrite a teammate's text."""
        conflict = {**self.existing, "text": "Другой вариант", "updated_at": "later"}
        self.package([self.new, conflict])
        before = self.target.read_bytes()
        result = merge_handoff(self.zip, self.project)
        self.assertEqual([item["status"] for item in result["items"]], ["conflict", "new"])
        with self.assertRaisesRegex(CatalogError, "resolve every"):
            merge_handoff(self.zip, self.project, apply=True)
        self.assertEqual(self.target.read_bytes(), before)
        reviewed = merge_handoff(self.zip, self.project, apply=True,
                                 choices={"RU_RU:TXT_KEY_EXISTING": "keep"})
        self.assertTrue(reviewed["applied"])
        self.assertEqual(csv_records(self.target.read_bytes(), "RU_RU")["TXT_KEY_EXISTING"]["text"],
                         "Существующий")

    def test_stale_english_or_invalid_tokens_block_write(self):
        """Only current English fingerprints and the required icon survive."""
        original = self.target.read_bytes()
        for bad in ({**self.new, "source_fingerprint": "c" * 64},
                    {**self.new, "text": "Новый без иконки"}):
            with self.subTest(bad=bad):
                self.package([bad])
                result = merge_handoff(self.zip, self.project)
                self.assertEqual(result["items"][0]["status"], "stale")
                with self.assertRaisesRegex(CatalogError, "resolve every"):
                    merge_handoff(self.zip, self.project, apply=True)
                self.assertEqual(self.target.read_bytes(), original)

    def test_note_only_conflict_is_visible_and_cannot_be_silently_replaced(self):
        """The review exposes changed notes even when translated text is identical."""
        changed_note = {**self.existing, "translator_note": "Different review note"}
        self.package([changed_note])
        report = merge_handoff(self.zip, self.project)
        self.assertEqual(report["items"][0]["status"], "conflict")
        self.assertEqual(report["items"][0]["team"], report["items"][0]["incoming"])
        self.assertEqual(report["items"][0]["incoming_note"], "Different review note")
        with self.assertRaisesRegex(CatalogError, "resolve every"):
            merge_handoff(self.zip, self.project, apply=True)

    def test_wrong_reference_or_stale_workspace_is_rejected(self):
        """Neither a foreign baseline nor old generated rows can approve a merge."""
        self.package([self.new], reference=b"different baseline")
        with self.assertRaisesRegex(CatalogError, "vanilla reference"):
            merge_handoff(self.zip, self.project, apply=True)
        self.package([self.new])
        self.primary.write_text("English was updated", encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, "prepare again"):
            merge_handoff(self.zip, self.project, apply=True)
        self.primary.write_text("<Language_en_US />", encoding="utf-8")
        self.game.write_text(self.game.read_text(encoding="utf-8").replace(
            "<Language_en_US>\n", '<Language_en_US>\n\t<!-- changed -->\n'),
            encoding="utf-8")
        with self.assertRaisesRegex(CatalogError, "prepare again"):
            merge_handoff(self.zip, self.project, apply=True)

    def test_generated_translation_change_does_not_stale_english_index(self):
        """A previous save changes the fallback XML but does not change English."""
        self.package([self.new])
        self.game.write_text(self.game.read_text(encoding="utf-8") +
                             "<!-- generated RU fallback changed -->", encoding="utf-8")
        result = merge_handoff(self.zip, self.project)
        self.assertEqual(result["items"][0]["status"], "new")

    def test_multi_language_bundle_and_reviewed_conflict(self):
        """Two selected languages merge together, with a deliberate overwrite decision."""
        de_target = self.target.with_name("DE_DE.csv")
        de_target.write_bytes(self.csv_bytes([]))
        de = self.editor / "DE_DE"
        de.mkdir()
        (de / "buildings.csv").write_text(
            "key,source_fingerprint,required_format_tokens\n"
            'TXT_KEY_NEW,' + "b" * 64 + ',"{""[ICON_CULTURE]"":1}"\n',
            encoding="utf-8-sig")
        manifest = self.editor / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["locales"]["DE_DE"] = {"files": {"buildings.csv": 1}}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        changed = {**self.existing, "text": "Обновлённый"}
        german = {**self.new, "text": "Neu [ICON_CULTURE]"}
        contents = {"RU_RU": self.csv_bytes([changed]),
                    "DE_DE": self.csv_bytes([german])}
        metadata = {"locales": {locale: sha256(content).hexdigest()
                                for locale, content in contents.items()},
                    "vanilla_reference_sha256": sha256(self.reference.read_bytes()).hexdigest()}
        with zipfile.ZipFile(self.zip, "w") as package:
            package.writestr("manifest.json", json.dumps(metadata))
            package.writestr("README.txt", "review")
            for locale, content in contents.items():
                package.writestr(f"translations/{locale}.csv", content)
        before = self.target.read_bytes()
        preview = merge_handoff(self.zip, self.project)
        self.assertEqual(preview["locales"], ["DE_DE", "RU_RU"])
        with self.assertRaisesRegex(CatalogError, "changed since preview"):
            merge_handoff(self.zip, self.project, apply=True,
                          choices={"RU_RU:TXT_KEY_EXISTING": "incoming"},
                          expected={**preview["target_sha256"], "RU_RU": "0" * 64})
        self.assertEqual(self.target.read_bytes(), before)
        result = merge_handoff(self.zip, self.project, apply=True,
                               choices={"RU_RU:TXT_KEY_EXISTING": "incoming"},
                               expected=preview["target_sha256"])
        self.assertTrue(result["applied"])
        self.assertEqual(csv_records(self.target.read_bytes(), "RU")["TXT_KEY_EXISTING"]["text"],
                         "Обновлённый")
        self.assertEqual(csv_records(de_target.read_bytes(), "DE")["TXT_KEY_NEW"]["text"],
                         "Neu [ICON_CULTURE]")


if __name__ == "__main__":
    unittest.main()
