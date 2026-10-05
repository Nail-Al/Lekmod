"""A shared ciphertext must never bypass the pinned vanilla reference."""

from contextlib import closing
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.vanilla_snapshot import write_snapshot
from lekmod_localization.vanilla_reference import write_reference
from snapshot_cloud import decrypt_snapshot, download_encrypted, encrypt_snapshot, MAGIC
from editor_server import Editor


class CloudSnapshotTests(unittest.TestCase):
    def test_empty_or_invalid_links_never_start_a_network_download(self):
        """Distinguish an empty placeholder from a real HTTPS file address."""
        with patch("snapshot_cloud.urllib.request.urlopen") as download:
            for url in ("", "   ", "https://", "http://example.org/reference.enc",
                        "https://user:password@example.org/reference.enc",
                        "https://example.org:invalid/reference.enc", "https://example.org/a b.enc"):
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, "link"):
                    download_encrypted(url)
            download.assert_not_called()
        url = "https://example.org/reference.enc"
        response = io.BytesIO(MAGIC + b"encrypted archive")
        response.url = url
        with patch("snapshot_cloud.urllib.request.urlopen", return_value=response) as download:
            self.assertEqual(download_encrypted("  " + url + "  "), MAGIC + b"encrypted archive")
            self.assertEqual(download.call_args.args[0].full_url, url)

    def test_missing_encryption_password_does_not_download_or_replace_a_snapshot(self):
        """Explain which password is needed before any existing reference is touched."""
        editor = Editor.__new__(Editor)
        editor.ready = True
        with patch("editor_server.download_encrypted") as download, \
             patch.object(editor, "import_snapshot") as install:
            with self.assertRaisesRegex(ValueError, "snapshot encryption password"):
                editor.import_cloud_snapshot("https://example.org/reference.enc", "")
            download.assert_not_called()
            install.assert_not_called()

    def test_password_and_reference_are_both_required(self):
        """Wrong passwords, tampering and other game tables all fail closed."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "vanilla.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE Language_en_US "
                                   "(Tag TEXT, Text TEXT, Gender TEXT, Plurality TEXT)")
                connection.execute("INSERT INTO Language_en_US VALUES (?, ?, ?, ?)",
                                   ("TXT_KEY_TEST", "Official text", None, None))
                connection.commit()
            snapshot = root / "snapshot.json.gz"
            reference = root / "reference.json.gz"
            archive = root / "snapshot.enc"
            write_snapshot(database, snapshot)
            write_reference(snapshot, reference)
            password = "a-long-unique-team-passphrase"
            encrypt_snapshot(snapshot, password, reference, archive)
            encrypted = archive.read_bytes()
            self.assertNotIn(b"Official text", encrypted)
            self.assertEqual(decrypt_snapshot(encrypted, password, reference),
                             snapshot.read_bytes())
            with self.assertRaisesRegex(ValueError, "wrong password"):
                decrypt_snapshot(encrypted, "a-completely-wrong-password", reference)
            with self.assertRaisesRegex(ValueError, "wrong password"):
                decrypt_snapshot(encrypted[:-1] + bytes([encrypted[-1] ^ 1]),
                                 password, reference)
            with self.assertRaisesRegex(ValueError, "at least 16"):
                decrypt_snapshot(encrypted, "111", reference)

            # The bundled editor must use the same verified format without
            # requiring contributors to install Python packages themselves.
            project_reference = root / "localization/reference/vanilla-fingerprints.json.gz"
            project_reference.parent.mkdir(parents=True)
            project_reference.write_bytes(reference.read_bytes())
            editor = Editor.__new__(Editor)
            editor.ready = True
            editor.snapshot = snapshot
            editor.last_encrypted_archive = None
            with patch("editor_server.APP_HOME", root), patch("editor_server.REPO_ROOT", root):
                first = editor.encrypt_local_snapshot(password)
                second = editor.encrypt_local_snapshot(password)
            self.assertNotEqual(first["filename"], second["filename"])
            self.assertEqual(editor.last_encrypted_archive, Path(second["path"]))
            self.assertEqual(decrypt_snapshot(Path(first["path"]).read_bytes(),
                                             password, reference), snapshot.read_bytes())


if __name__ == "__main__":
    unittest.main()
