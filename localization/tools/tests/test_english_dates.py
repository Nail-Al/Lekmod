"""Portable English dates survive line ending conversion on Windows."""

import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization.english_dates import read_dates, source_hash


class PortableDatesTests(unittest.TestCase):
    def test_windows_checkout_matches_the_same_index(self):
        """A CRLF checkout retains commit dates from the LF reference."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "localization/en_US/primary.xml"
            reference = root / "localization/reference/english-edit-dates.json.gz"
            source.parent.mkdir(parents=True)
            reference.parent.mkdir(parents=True)
            lf = b"<Row>\n<Text>English</Text>\n</Row>\n"
            source.write_bytes(lf)
            original_hash = source_hash(source)
            reference.write_bytes(gzip.compress(json.dumps({
                "source_sha256": original_hash,
                "dates": {"TXT_KEY_EXAMPLE": "2026-09-27T00:00:00+00:00"},
            }).encode()))
            source.write_bytes(lf.replace(b"\n", b"\r\n"))
            self.assertEqual(source_hash(source), original_hash)
            self.assertEqual(read_dates(source, [], root)["TXT_KEY_EXAMPLE"],
                             "2026-09-27T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
