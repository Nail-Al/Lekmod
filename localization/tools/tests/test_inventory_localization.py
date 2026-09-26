from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inventory_localization as inv


class InventoryTests(unittest.TestCase):
    """Verify conservative discovery of localization definitions and uses."""

    def setUp(self):
        """Create an isolated Git-like tree for each inventory scenario."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.paths = []

    def source(self, path, content):
        """Write a text or byte fixture at a repository-relative path."""
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        self.paths.append(path)
        return destination

    def scan(self, require_primary=False):
        """Run inventory against the fixture paths collected by the test."""
        return inv.inventory(self.root, self.paths, require_primary=require_primary)

    def test_xml_operations_preserve_fields_order_and_metadata_only_updates(self):
        """Record XML operations exactly enough for later semantic review."""
        self.source(inv.PRIMARY_TEXT, '''<?xml version="1.0" encoding="utf-8"?>
<GameData><Language_en_US>
<!-- <Row Tag="TXT_KEY_COMMENT"><Text>Not active</Text></Row> -->
<Row Tag="TXT_KEY_TEST" Text="First &amp; second"><Gender>neuter:an</Gender></Row>
<Replace><Tag>TXT_KEY_TEST</Tag><Text>  Next\ntext [ICON_FOOD] {1_Num}  </Text><Plurality>1</Plurality></Replace>
<Update><Where Tag="TXT_KEY_TEST"/><Set Gender="feminine"/></Update>
<Update><Where><Tag>TXT_KEY_TEST</Tag></Where><Set><Text>Newest</Text></Set></Update>
<Delete Tag="TXT_KEY_TEST"/>
<Delete/>
</Language_en_US><Language_RU_RU/></GameData>''')
        report = self.scan(require_primary=True)
        ops = report["operations"]
        self.assertEqual([op["operation"] for op in ops], ["Row", "Replace", "Update", "Update", "Delete", "Delete"])
        self.assertEqual(ops[0]["fields"], {"Tag": "TXT_KEY_TEST", "Text": "First & second", "Gender": "neuter:an"})
        self.assertEqual(ops[0]["line"], 4)
        self.assertEqual(ops[1]["fields"]["Text"], "  Next\ntext [ICON_FOOD] {1_Num}  ")
        self.assertEqual(ops[1]["fields"]["Plurality"], "1")
        self.assertNotIn("Text", ops[2]["fields"])
        self.assertEqual(ops[3]["where"], {"Tag": "TXT_KEY_TEST"})
        self.assertEqual(ops[-1]["where"], {})
        self.assertIsNone(ops[-1]["key"])
        self.assertEqual(report["language_blocks"][-1]["locale"], "RU_RU")
        self.assertEqual(report["summary"]["errors"], 0)

    def test_repeated_replace_is_evidence_not_an_automatic_error(self):
        """Report repeated writes without assuming an unknown load order."""
        self.source(inv.PRIMARY_TEXT, '''<GameData><Language_en_US>
<Replace Tag="TXT_KEY_A"><Text>Earlier</Text></Replace>
<Replace Tag="TXT_KEY_A"><Text>Later</Text></Replace>
</Language_en_US></GameData>''')
        report = self.scan()
        self.assertEqual(report["summary"]["repeated_write_targets"], 1)
        self.assertEqual(report["repeated_write_targets"][0]["operation_indices"], [0, 1])
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertEqual(report["coverage"]["runtime_load_order"], "not_resolved")

    def test_unresolved_key_casing_and_empty_templates_are_visible(self):
        """Keep malformed selectors and templates visible to developers."""
        self.source(inv.PRIMARY_TEXT, '''<GameData><Language_en_US>
<Row tag="TXT_KEY_LOWERCASE"><Text>Retained for review</Text></Row>
<Row Tag=""><Text>Template</Text></Row>
</Language_en_US></GameData>''')
        report = self.scan()
        self.assertEqual(report["operations"][0]["fields"]["tag"], "TXT_KEY_LOWERCASE")
        self.assertIsNone(report["operations"][0]["key"])
        self.assertEqual(report["summary"]["warnings"], 2)
        self.assertEqual(report["literals"][0]["key_tokens"], ["TXT_KEY_LOWERCASE"])

    def test_area_boundaries_do_not_conflate_source_and_override(self):
        """Distinguish primary Override data from other repository areas."""
        for path, text in [("LEKMOD/Art/source.xml", "200 Faith"), (inv.PRIMARY_TEXT, "100 Faith")]:
            self.source(path, f'<GameData><Language_en_US><Row Tag="TXT_KEY_CATHEDRALS"><Text>{text}</Text></Row></Language_en_US></GameData>')
        report = self.scan()
        self.assertEqual([op["scope"] for op in report["operations"]], ["art", "override"])
        self.assertEqual(report["summary"]["repeated_write_targets"], 0)
        self.assertEqual(report["summary"]["cross_source_write_targets"], 1)
        self.assertEqual(report["summary"]["cross_source_text_differences"], 1)
        self.assertTrue(report["cross_source_write_targets"][0]["different_text"])
        self.assertEqual(len(report["operations"]), 2)

    def test_art_only_english_keys_are_reported_without_choosing_load_order(self):
        """Expose Art-only English keys without claiming runtime precedence."""
        self.source(
            "LEKMOD/Art/source.xml",
            '<GameData><Language_en_US><Row Tag="TXT_KEY_ART_ONLY" '
            'Text="Art text"/></Language_en_US></GameData>',
        )
        self.source(
            inv.PRIMARY_TEXT,
            '<GameData><Language_en_US><Row Tag="TXT_KEY_PRIMARY" '
            'Text="Primary text"/></Language_en_US></GameData>',
        )
        report = self.scan()
        self.assertEqual(
            report["art_english_keys_not_in_override"],
            ["TXT_KEY_ART_ONLY"],
        )
        self.assertEqual(report["summary"]["art_english_keys_not_in_override"], 1)
        self.assertEqual(report["coverage"]["runtime_load_order"], "not_resolved")

    def test_uppercase_extensions_and_ui_templates_are_included(self):
        """Discover case variants and disabled UI templates intentionally."""
        self.source("LEKMOD/Art/text.XML", '<GameData><Language_en_US><Replace Tag="TXT_KEY_A" Text="A"/></Language_en_US></GameData>')
        self.source("LEKMOD/Lua/tmp/eui/Screen.LUA.IGNORE", '-- "TXT_KEY_COMMENT"\ncontrol:SetText("Visible text")\nL"TXT_KEY_HELLO"')
        self.source("LEKMOD/Lua/tmp/ui/Screen.XML.IGNORE", '<Context><Label String="Ready" ToolTip="TXT_KEY_HINT"/></Context>')
        report = self.scan()
        self.assertEqual(len(report["operations"]), 1)
        self.assertEqual({x["value"] for x in report["literals"]}, {"Visible text", "TXT_KEY_HELLO", "Ready", "TXT_KEY_HINT"})
        self.assertTrue(all(x["scope"] == "ui_template" for x in report["literals"]))

    def test_lua_lexer_handles_long_comments_long_strings_and_escapes(self):
        """Avoid losing Lua keys around comments, long strings, and escapes."""
        text = '''--[==[ "TXT_KEY_HIDDEN" ]==]
local a = [=[line one
TXT_KEY_VISIBLE]=]
local b = "escaped \\\"quote\\\" -- still a string"
-- "another hidden literal"
local prefix = "TXT_KEY_DYNAMIC_" .. name
'''
        self.source("Lekmap/map.lua", text)
        report = self.scan()
        values = [x["value"] for x in report["literals"]]
        self.assertEqual(values, ["line one\nTXT_KEY_VISIBLE", 'escaped \\"quote\\" -- still a string', "TXT_KEY_DYNAMIC_"])
        self.assertEqual(report["literals"][0]["line"], 2)
        self.assertTrue(all("review_status" not in x for x in report["literals"]))

    def test_cpp_raw_strings_and_non_utf8_comments_do_not_hide_keys(self):
        """Scan C++ raw strings while preserving undecodable source bytes."""
        self.source("LEKMOD_DLL/Text.cpp", b'// Copyright \xa9\n/* "TXT_KEY_COMMENT" */\nauto a = R"tag(one "quote" TXT_KEY_RAW)tag";\nLookup("TXT_KEY_REAL");\nconst char *b = "caf\xe9";')
        report = self.scan()
        values = report["literals"]
        self.assertEqual(values[0]["value"], 'one "quote" TXT_KEY_RAW')
        self.assertEqual(values[1]["key_tokens"], ["TXT_KEY_REAL"])
        self.assertEqual(values[2]["raw_bytes_hex"], b"caf\xe9".hex())
        self.assertEqual(values[2]["value"], "caf\\xe9")
        self.assertEqual(len(report["encoding_notes"]), 1)
        self.assertEqual([x["code"] for x in report["issues"]], ["literal_encoding_unresolved"])
        # A report with undecodable bytes must still be valid UTF-8 JSON.
        json.dumps(report, ensure_ascii=False).encode("utf-8")

    def test_sql_is_not_executed_and_strings_are_not_table_mentions(self):
        """Treat SQL as source text and ignore table names inside literals."""
        self.source("LEKMOD/Art/text.sql", '''-- UPDATE Language_FAKE SET Text='x';
INSERT OR REPLACE INTO "Language_en_US" (Tag, Text)
VALUES ('TXT_KEY_SQL', 'O''Brien: Language_NOT_A_TABLE');
/* DELETE FROM Language_COMMENT; */
UPDATE Language_RU_RU SET Gender='feminine' WHERE Tag='TXT_KEY_SQL';''')
        report = self.scan()
        self.assertEqual([m["table"] for m in report["sql_language_mentions"]], ["Language_en_US", "Language_RU_RU"])
        self.assertEqual(report["operations"], [])
        self.assertIn("O''Brien: Language_NOT_A_TABLE", [x["value"] for x in report["literals"]])

    def test_python_fstrings_are_visible_across_python_versions(self):
        """Find Python strings without relying on host AST implementation."""
        self.source("LekmodInstaller/main.py", '# "hidden"\nlabel = f"Installing {version}"\nbutton = "Continue"\n')
        report = self.scan()
        values = [x["value"] for x in report["literals"]]
        self.assertTrue(any("Installing" in value for value in values))
        self.assertTrue(any("Continue" in value for value in values))
        self.assertFalse(any("hidden" in value for value in values))

    def test_existing_empty_override_stubs_do_not_hide_empty_primary(self):
        """Require the real primary source even when empty stubs are present."""
        self.source("LEKMOD/Override/CIV5GameTextInfos.XML", b"")
        self.source(inv.PRIMARY_TEXT, b"")
        report = self.scan(require_primary=True)
        self.assertEqual(report["summary"]["file_statuses"]["empty_override_stub"], 2)
        self.assertEqual({x["code"] for x in report["issues"]}, {"empty_xml", "missing_primary_text"})

    def test_invalid_data_is_an_error_but_legacy_ui_gap_stays_visible(self):
        """Separate invalid inputs from expected manual-review gaps."""
        self.source("LEKMOD/Art/broken.xml", "<GameData>")
        self.source(
            "LEKMOD/Lua/tmp/eui/broken.xml.ignore",
            '<Context><Label String="TXT_KEY_RAW" String="Visible text"/></Context>',
        )
        report = self.scan()
        self.assertEqual(report["summary"]["errors"], 1)
        self.assertEqual(report["summary"]["warnings"], 1)
        self.assertEqual(report["literals"][0]["kind"], "unparsed_xml_key_candidate")
        self.assertEqual(report["literals"][1]["value"], "Visible text")
        self.assertEqual(report["operations"], [])

    def test_unsupported_xml_shapes_are_not_silently_accepted(self):
        """Warn when valid XML contains operations with ambiguous meaning."""
        self.source("LEKMOD/Art/unknown.xml", '''<GameData><Language_en_US>
<Merge Tag="TXT_KEY_A"/>
<Row Tag="TXT_KEY_A"><Text>A</Text><Text>B</Text></Row>
<Update><Set Text="C"/><Where Tag="TXT_KEY_A"/><Where Tag="TXT_KEY_B"/></Update>
</Language_en_US></GameData>''')
        report = self.scan()
        self.assertEqual(report["summary"]["errors"], 3)
        self.assertTrue(all(op["unparsed"] for op in report["operations"]))

    def test_doctype_is_rejected_without_reading_external_files(self):
        """Reject document types so inventory never resolves external data."""
        self.source("LEKMOD/Art/external.xml", '<!DOCTYPE GameData [<!ENTITY x SYSTEM "file:///not-readable">]><GameData>&x;</GameData>')
        report = self.scan()
        self.assertEqual(report["summary"]["errors"], 1)
        self.assertIn("DOCTYPE", report["issues"][0]["message"])

    def test_scanning_is_read_only_and_output_is_deterministic(self):
        """Produce stable reports without changing scanned repository files."""
        self.source(inv.PRIMARY_TEXT, '<GameData><Language_en_US><Row Tag="TXT_KEY_A"><Text>Test</Text></Row></Language_en_US></GameData>')
        self.source("Lekmap/map.lua", 'return { Name = "Hello" }')
        before = {p: hashlib.sha256((self.root / p).read_bytes()).hexdigest() for p in self.paths}
        a = self.scan(require_primary=True)
        b = inv.inventory(self.root, list(reversed(self.paths)))
        self.assertEqual(a, b)
        self.assertEqual(before, {p: hashlib.sha256((self.root / p).read_bytes()).hexdigest() for p in self.paths})

    def test_report_cannot_overwrite_input_or_be_written_into_game_data(self):
        """Keep generated reports away from source and shipped game paths."""
        source = self.source("LekmodInstaller/config.json", '{"keep": true}')
        report = self.scan()
        with self.assertRaises(ValueError):
            inv.write_report(source, report, self.root, self.paths)
        with self.assertRaises(ValueError):
            inv.write_report(self.root / "LEKMOD/new-report.json", report, self.root, self.paths)
        with self.assertRaises(ValueError):
            inv.write_report(self.root / "report.xml", report, self.root, self.paths)
        self.assertEqual(source.read_text(), '{"keep": true}')
        output = self.root / "localization/workspace/inventory.json"
        inv.write_report(output, report, self.root, self.paths)
        inv.write_report(output, report, self.root, self.paths)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_git_discovery_includes_untracked_sources_but_excludes_build_output(self):
        """Find live sources while excluding ignored and deleted paths."""
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.source(".gitignore", "build/\n")
        self.source("LEKMOD/Art/new.xml", "<GameData/>")
        self.source("LEKMOD/Lua/tmp/ui/Menu.lua.ignore", 'return "Menu"')
        self.source("build/localization/report.json", "{}")
        deleted = self.source("LEKMOD/Art/deleted.xml", "<GameData/>")
        subprocess.run(
            ["git", "add", "--", "LEKMOD/Art/deleted.xml"],
            cwd=self.root,
            check=True,
        )
        deleted.unlink()
        paths = inv.git_files(self.root)
        self.assertIn("LEKMOD/Art/new.xml", paths)
        self.assertIn("LEKMOD/Lua/tmp/ui/Menu.lua.ignore", paths)
        self.assertNotIn("LEKMOD/Art/deleted.xml", paths)
        self.assertNotIn("build/localization/report.json", paths)

    def test_cli_strict_fails_on_invalid_data_but_not_on_review_findings(self):
        """Fail CI for invalid data, not unresolved review evidence."""
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.source(inv.PRIMARY_TEXT, '<GameData><Language_en_US><Row Tag="TXT_KEY_A" Text="Test"/></Language_en_US></GameData>')
        subprocess.run(["git", "add", "--", inv.PRIMARY_TEXT], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Inventory fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-q", "-m", "Fixture"], cwd=self.root, check=True)
        self.source("LEKMOD/Lua/tmp/eui/broken.xml.ignore", "<Context>")
        command = [
            sys.executable,
            str(Path(inv.__file__).resolve()),
            "--root",
            str(self.root),
            "--strict",
        ]
        passed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("0 errors, 1 warnings", passed.stdout)
        self.source("LEKMOD/Art/broken.xml", "<GameData>")
        failed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("1 errors, 1 warnings", failed.stdout)


if __name__ == "__main__":
    unittest.main()
