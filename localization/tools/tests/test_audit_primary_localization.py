from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_primary_localization as audit


class PrimaryLocalizationAuditTests(unittest.TestCase):
    """Protect parsing of the shipped primary English localization source."""

    def setUp(self):
        """Create an isolated source tree for every parser test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, text: str) -> Path:
        """Write one fixture without depending on repository game data."""
        path = self.root / "source.xml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_operations_follow_source_order_and_preserve_metadata(self):
        """Apply supported operations in order while retaining all columns."""
        source = self.source('''<GameData><Language_en_US>
<Row Tag="TXT_KEY_A"><Text>First</Text><Gender>masculine</Gender></Row>
<Replace Tag="TXT_KEY_A"><Text>  Second [ICON_FOOD]\n  </Text><Plurality>2</Plurality></Replace>
<Update><Where Tag="TXT_KEY_A"/><Set Gender="feminine"/></Update>
<Row tag="TXT_KEY_B" Text="Third"/>
<Delete Tag="TXT_KEY_B"/>
</Language_en_US></GameData>''')
        report = audit.parse_source(source, "en_US")

        self.assertEqual(
            report["summary"]["operations"],
            {"Delete": 1, "Replace": 1, "Row": 2, "Update": 1},
        )
        self.assertEqual(report["entries"]["TXT_KEY_A"]["Text"], "  Second [ICON_FOOD]\n  ")
        self.assertEqual(report["entries"]["TXT_KEY_A"]["Gender"], "feminine")
        self.assertEqual(report["entries"]["TXT_KEY_A"]["Plurality"], "2")
        self.assertNotIn("TXT_KEY_B", report["entries"])
        self.assertEqual(report["repeated_writes"], {"TXT_KEY_A": [0, 1, 2]})
        self.assertEqual(report["summary"]["warnings"], 1)
        self.assertEqual(report["summary"]["errors"], 0)

    def test_unsupported_and_ambiguous_operations_are_errors(self):
        """Reject shapes whose runtime meaning cannot be inferred safely."""
        source = self.source('''<GameData><Language_en_US>
<Merge Tag="TXT_KEY_A"/>
<Row Tag="TXT_KEY_A" tag="TXT_KEY_B"><Text>A</Text></Row>
<Update><Where Tag="TXT_KEY_A"/><Where Tag="TXT_KEY_B"/><Set Text="B"/></Update>
</Language_en_US></GameData>''')
        report = audit.parse_source(source, "en_US")

        self.assertEqual(report["summary"]["errors"], 3)
        self.assertTrue(all(operation["invalid"] for operation in report["operations"]))

    def test_missing_locale_and_invalid_xml_fail(self):
        """Treat unusable XML and absent language tables as hard errors."""
        missing = audit.parse_source(self.source("<GameData/>"), "en_US")
        self.assertEqual(missing["summary"]["errors"], 1)

        broken = audit.parse_source(self.source("<GameData>"), "en_US")
        self.assertEqual(broken["summary"]["errors"], 1)

    def test_json_report_does_not_modify_source(self):
        """Write reports atomically without touching the checked source."""
        source = self.source(
            '<GameData><Language_en_US><Row Tag="TXT_KEY_A" Text="A"/></Language_en_US></GameData>'
        )
        original = source.read_bytes()
        report = audit.parse_source(source, "en_US")
        output = self.root / "build" / "primary.json"

        audit.write_report(output, report, source)

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
        with self.assertRaises(ValueError):
            audit.write_report(source, report, source)
        with self.assertRaises(ValueError):
            audit.write_report(audit.REPO_ROOT / "LEKMOD" / "report.json", report, source)

    def test_repository_primary_source_is_covered(self):
        """Keep the parser compatible with the full checked-in modpack XML."""
        report = audit.parse_source(audit.DEFAULT_SOURCE, "en_US")

        self.assertEqual(report["summary"]["errors"], 0)
        self.assertGreater(report["summary"]["entries"], 30_000)
        self.assertGreater(report["summary"]["operations"].get("Replace", 0), 27_000)


if __name__ == "__main__":
    unittest.main()
