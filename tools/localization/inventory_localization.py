"""Read-only source inventory; this does not reconstruct Civ V's loaded database."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tokenize
from typing import Iterator
from xml.parsers import expat


REPO_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_TEXT = "LEKMOD/Override/CIV5Units_Mongol.xml"
SOURCE_ROOTS = {"LEKMOD", "Lekmap", "LEKMOD_DLL", "LekmodInstaller"}
KEY_RE = re.compile(r"\bTXT_KEY_[A-Za-z0-9_]+")
LANGUAGE_RE = re.compile(r"^Language_([A-Za-z0-9_]+)$")
SQL_LANGUAGE_RE = re.compile(r"\bLanguage_[A-Za-z0-9_]+\b", re.IGNORECASE)
UI_ATTRIBUTE_RE = re.compile(
    r"\b(String|Text|ToolTip|ToolTipString|Title)\s*=\s*([\"'])(.*?)\2"
)
LONG_LUA_RE = re.compile(r"\[(=*)\[")
RAW_CPP_RE = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')
UI_FIELDS = {"String", "Text", "ToolTip", "ToolTipString", "Title"}
DATA_TEXT_FIELDS = {
    "Name", "Description", "ShortDescription", "Adjective", "Help",
    "Civilopedia", "Quote", "Text", "Title", "Caption", "Teaser",
}
CODE_EXTENSIONS = {".lua", ".cpp", ".h", ".hpp", ".c", ".inl", ".py", ".sql"}
ASSET_EXTENSIONS = {".dds", ".png", ".jpg", ".jpeg", ".tga", ".fpk", ".ttf", ".otf"}


@dataclass
class XmlNode:
    tag: str
    attrs: dict[str, str]
    line: int
    children: list[XmlNode] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def child(self, name: str) -> XmlNode | None:
        return next((c for c in self.children if c.tag == name), None)


def parse_xml(data: bytes) -> XmlNode:
    """Keep source line numbers and decoded text, including whitespace."""
    parser = expat.ParserCreate(namespace_separator="}")
    stack: list[XmlNode] = []
    roots: list[XmlNode] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        node = XmlNode(name.rsplit("}", 1)[-1], attrs, parser.CurrentLineNumber)
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    def end(name: str) -> None:
        node = stack.pop()
        # Only scalar values need their text. Do not duplicate entire documents.
        if stack and stack[-1].tag in {"Text", "Gender", "Plurality", "Tag"}:
            stack[-1].parts.append(node.text)

    def characters(value: str) -> None:
        if stack:
            stack[-1].parts.append(value)

    def reject_doctype(*args: object) -> None:
        raise ValueError("DOCTYPE declarations are not supported by the inventory")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.Parse(data, True)
    return roots[0]


def columns(node: XmlNode | None) -> dict[str, str]:
    if node is None:
        return {}
    values = dict(node.attrs)
    for child in node.children:
        if child.tag in values or child.children:
            raise ValueError(f"Ambiguous or nested column: {child.tag}")
        values[child.tag] = child.text
    return values


def effective_extension(path: str) -> str:
    name = path.lower()
    if name.endswith(".ignore"):
        name = name[:-7]
    return Path(name).suffix


def scope_for(path: str) -> str:
    if path.startswith("LEKMOD/Override/"):
        return "override"
    if path.startswith("LEKMOD/Art/"):
        return "art"
    if path.startswith("LEKMOD/Lua/tmp/"):
        return "ui_template"
    if path.startswith("LEKMOD/Lua/"):
        return "lua"
    if path.startswith("Lekmap/"):
        return "lekmap"
    if path.startswith("LEKMOD_DLL/"):
        return "dll_source"
    if path.startswith("LekmodInstaller/"):
        return "installer"
    return "other"


def git_files(root: Path) -> list[str]:
    # Git's list includes .ignore templates and tracked files ignored by .gitignore.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    return sorted(set(p for p in result.stdout.decode("utf-8").split("\0") if p))


def add_issue(report: dict, severity: str, path: str, line: int, code: str, message: str) -> None:
    report["issues"].append({
        "severity": severity, "path": path, "line": line,
        "code": code, "message": message,
    })


def add_literal(report: dict, path: str, line: int, kind: str, value: str, context: str) -> None:
    # Source-language escapes in code literals are intentionally not evaluated.
    record = {
        "path": path, "line": line, "scope": scope_for(path), "kind": kind,
        "value": value, "context": context,
        "key_tokens": sorted(set(KEY_RE.findall(value))),
    }
    if any(0xDC80 <= ord(ch) <= 0xDCFF for ch in value):
        # Preserve bytes without guessing a legacy code page or emitting invalid JSON.
        record["raw_bytes_hex"] = value.encode("utf-8", errors="surrogateescape").hex()
        record["value"] = "".join(
            f"\\x{ord(ch) - 0xDC00:02x}" if 0xDC80 <= ord(ch) <= 0xDCFF else ch
            for ch in value
        )
        record["encoding_unresolved"] = True
        add_issue(report, "warning", path, line, "literal_encoding_unresolved",
                  "Literal contains non-UTF-8 bytes; inspect raw_bytes_hex before translating")
    report["literals"].append(record)


def scan_xml(report: dict, path: str, data: bytes) -> str:
    try:
        root = parse_xml(data)
    except (expat.ExpatError, ValueError) as error:
        # UI templates have their own dialect/legacy defects. Report the gap;
        # their raw tokens and UI attributes remain candidates, never parsed
        # definitions.
        ui = scope_for(path) in {"ui_template", "lua"}
        add_issue(report, "warning" if ui else "error", path,
                  getattr(error, "lineno", 1), "xml_parse_error", str(error))
        for line, text in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if KEY_RE.search(text):
                add_literal(report, path, line, "unparsed_xml_key_candidate", text, "raw line; may include comments")
            for match in UI_ATTRIBUTE_RE.finditer(text):
                value = match[3]
                if value and not KEY_RE.search(value):
                    add_literal(
                        report,
                        path,
                        line,
                        "unparsed_xml_ui_attribute",
                        value,
                        f"raw {match[1]} attribute; may include comments",
                    )
        return "unparsed_xml"

    block_index = 0

    def visit(node: XmlNode, parent: str) -> None:
        nonlocal block_index
        language = LANGUAGE_RE.fullmatch(node.tag)
        if language:
            block_index += 1
            block = block_index
            report["language_blocks"].append({
                "path": path, "line": node.line, "scope": scope_for(path),
                "table": node.tag, "locale": language[1], "block": block,
                "operation_count": len(node.children),
            })
            for index, op in enumerate(node.children):
                record = {
                    "path": path, "line": op.line, "scope": scope_for(path),
                    "table": node.tag, "locale": language[1], "block": block,
                    "index": index, "operation": op.tag,
                    "key": None, "fields": {}, "where": {},
                }
                try:
                    if op.tag in {"Row", "Replace"}:
                        record["fields"] = columns(op)
                        record["key"] = record["fields"].get("Tag")
                    elif op.tag == "Update":
                        if [c.tag for c in op.children].count("Where") != 1 or [c.tag for c in op.children].count("Set") != 1:
                            raise ValueError("Update needs exactly one Where and one Set")
                        if op.attrs or any(c.tag not in {"Where", "Set"} for c in op.children):
                            raise ValueError("Unsupported Update shape")
                        record["fields"] = columns(op.child("Set"))
                        record["where"] = columns(op.child("Where"))
                        record["key"] = record["where"].get("Tag")
                    elif op.tag == "Delete":
                        record["where"] = columns(op)
                        record["key"] = record["where"].get("Tag")
                    else:
                        raise ValueError(f"Unsupported language-table operation: {op.tag}")
                except ValueError as error:
                    add_issue(report, "error", path, op.line, "unsupported_operation", str(error))
                    record["unparsed"] = True
                if op.tag in {"Row", "Replace"} and not record["key"] and not record.get("unparsed"):
                    add_issue(report, "warning", path, op.line, "unresolved_write_key",
                              "Row/Replace has no nonempty Tag column with canonical casing; raw fields retained")
                report["operations"].append(record)
                for name, value in record["fields"].items():
                    if name != "Tag" and KEY_RE.search(value):
                        add_literal(report, path, op.line, "language_field_reference", value, f"{node.tag}/{op.tag}/{name}")
            return

        for name, value in node.attrs.items():
            if KEY_RE.search(value) or (name in UI_FIELDS and value):
                add_literal(report, path, node.line, "xml_attribute", value, f"{node.tag}/@{name}")
        if not node.children and node.text.strip():
            if KEY_RE.search(node.text) or node.tag in DATA_TEXT_FIELDS:
                add_literal(report, path, node.line, "xml_field", node.text, f"{parent}/{node.tag}")
        for child in node.children:
            visit(child, node.tag)

    visit(root, "")
    return "xml_parsed"


def source_tokens(text: str, extension: str) -> Iterator[tuple[int, int, str, str]]:
    """Lexical inventory of Lua/C++/SQL strings and comments, not a code parser.

    Yields (start, end, kind, raw value). The caller masks comments for SQL
    table mentions. No input code or SQL is ever executed.
    """
    i = 0
    while i < len(text):
        start = i
        if extension in {".lua", ".sql"} and text.startswith("--", i):
            long = LONG_LUA_RE.match(text, i + 2) if extension == ".lua" else None
            if long:
                end_marker = "]" + long[1] + "]"
                end = text.find(end_marker, long.end())
                if end < 0:
                    raise ValueError(f"Unterminated long comment at offset {i}")
                i = end + len(end_marker)
            else:
                end = text.find("\n", i)
                i = len(text) if end < 0 else end
            yield start, i, "comment", text[start:i]
        elif extension != ".lua" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError(f"Unterminated block comment at offset {i}")
            i = end + 2
            yield start, i, "comment", text[start:i]
        elif extension not in {".lua", ".sql"} and text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            yield start, i, "comment", text[start:i]
        elif extension == ".lua" and (long := LONG_LUA_RE.match(text, i)):
            end_marker = "]" + long[1] + "]"
            end = text.find(end_marker, long.end())
            if end < 0:
                raise ValueError(f"Unterminated long string at offset {i}")
            i = end + len(end_marker)
            yield start, i, "lua_long_string", text[long.end():end]
        elif extension not in {".lua", ".sql"} and (raw := RAW_CPP_RE.match(text, i)):
            end_marker = ")" + raw[1] + '"'
            end = text.find(end_marker, raw.end())
            if end < 0:
                raise ValueError(f"Unterminated C++ raw string at offset {i}")
            i = end + len(end_marker)
            yield start, i, "cpp_raw_string", text[raw.end():end]
        elif text[i] in {'"', "'", "`"} and (text[i] != "`" or extension == ".sql"):
            quote = text[i]
            i += 1
            while i < len(text):
                if text[i] == quote:
                    if extension == ".sql" and i + 1 < len(text) and text[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                if text[i] == "\\" and extension != ".sql":
                    i += 2
                else:
                    i += 1
            else:
                raise ValueError(f"Unterminated quoted string at offset {start}")
            kind = "sql_quoted_identifier" if extension == ".sql" and quote != "'" else "source_string"
            yield start, i, kind, text[start + 1:i - 1]
        else:
            i += 1


def scan_code(report: dict, path: str, data: bytes, extension: str) -> str:
    try:
        if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            text = data.decode("utf-32")
        elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig", errors="surrogateescape")
    except UnicodeDecodeError as error:
        add_issue(report, "warning", path, 1, "encoding_unresolved", str(error))
        return "unparsed_encoding"
    undecoded = sum(0xDC80 <= ord(ch) <= 0xDCFF for ch in text)
    if undecoded:
        report["encoding_notes"].append({
            "path": path, "non_utf8_bytes": undecoded,
            "handling": "ASCII syntax scanned; undecodable literal bytes preserved as hex; no code page assumed",
        })
    if extension == ".py":
        try:
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type in {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1),
                                  getattr(tokenize, "TSTRING_MIDDLE", -1)}:
                    add_literal(report, path, token.start[0], "python_string_token", token.string, "raw token; not evaluated")
        except (tokenize.TokenError, IndentationError, SyntaxError) as error:
            add_issue(report, "warning", path, 1, "lexer_error", str(error))
            return "partially_scanned_code"
        return "lexically_scanned_code"

    newlines = [i for i, ch in enumerate(text) if ch == "\n"]
    sql_parts: list[str] = []
    previous = 0
    try:
        for start, end, kind, value in source_tokens(text, extension):
            if kind != "comment":
                add_literal(report, path, bisect_right(newlines, start) + 1, kind, value, "raw literal; use and escapes not resolved")
            if extension == ".sql":
                sql_parts.append(text[previous:start])
                # Preserve offsets while excluding comments and string VALUES.
                sql_parts.append(text[start:end] if kind == "sql_quoted_identifier" else " " * (end - start))
                previous = end
    except ValueError as error:
        add_issue(report, "warning", path, 1, "lexer_error", str(error))
        return "partially_scanned_code"
    if extension == ".sql":
        sql_parts.append(text[previous:])
        for match in SQL_LANGUAGE_RE.finditer("".join(sql_parts)):
            report["sql_language_mentions"].append({
                "path": path, "line": bisect_right(newlines, match.start()) + 1,
                "table": match[0], "scope": scope_for(path),
            })
    return "lexically_scanned_code"


def summarize(report: dict) -> dict:
    groups: dict[tuple[str, str], Counter] = defaultdict(Counter)
    keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    writes: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    cross_source_writes: dict[
        tuple[str, str], dict[str, list[int]]
    ] = defaultdict(lambda: defaultdict(list))
    text_by_source: dict[tuple[str, str, str], str] = {}

    for index, op in enumerate(report["operations"]):
        group = (op["scope"], op["table"])
        groups[group][op["operation"]] += 1
        if op["operation"] in {"Row", "Replace", "Update"} and op["key"]:
            keys[group].add(op["key"])
            writes[(*group, op["key"])].append(index)
            cross_source_writes[(op["table"], op["key"])][op["scope"]].append(index)

            if "Text" in op["fields"]:
                text_by_source[(op["table"], op["key"], op["scope"])] = (
                    " ".join(op["fields"]["Text"].split())
                )

    report["repeated_write_targets"] = [
        {"scope": scope, "table": table, "key": key, "operation_indices": indices}
        for (scope, table, key), indices in sorted(writes.items()) if len(indices) > 1
    ]
    report["cross_source_write_targets"] = []

    for (table, key), by_scope in sorted(cross_source_writes.items()):
        if len(by_scope) < 2:
            continue

        texts = {
            scope: text_by_source.get((table, key, scope))
            for scope in sorted(by_scope)
        }
        comparable_texts = {
            text
            for text in texts.values()
            if text is not None
        }
        report["cross_source_write_targets"].append({
            "table": table,
            "key": key,
            "operation_indices_by_scope": {
                scope: by_scope[scope]
                for scope in sorted(by_scope)
            },
            "text_by_scope": texts,
            "different_text": len(comparable_texts) > 1,
        })

    art_english = keys[("art", "Language_en_US")]
    override_english = keys[("override", "Language_en_US")]
    report["art_english_keys_not_in_override"] = sorted(
        art_english - override_english
    )

    return {
        "files": len(report["files"]),
        "file_statuses": dict(sorted(Counter(f["status"] for f in report["files"]).items())),
        "language_tables": [
            {"scope": scope, "table": table, "operations": dict(sorted(counts.items())),
             "distinct_write_selectors": len(keys[(scope, table)])}
            for (scope, table), counts in sorted(groups.items())
        ],
        "string_candidates": len(report["literals"]),
        "key_reference_candidates": sum(
            bool(item["key_tokens"])
            for item in report["literals"]
        ),
        "non_key_string_candidates": sum(
            not item["key_tokens"]
            for item in report["literals"]
        ),
        "sql_files_with_language_mentions": len({x["path"] for x in report["sql_language_mentions"]}),
        "repeated_write_targets": len(report["repeated_write_targets"]),
        "cross_source_write_targets": len(report["cross_source_write_targets"]),
        "cross_source_text_differences": sum(
            target["different_text"]
            for target in report["cross_source_write_targets"]
        ),
        "art_english_keys_not_in_override": len(
            report["art_english_keys_not_in_override"]
        ),
        "errors": sum(x["severity"] == "error" for x in report["issues"]),
        "warnings": sum(x["severity"] == "warning" for x in report["issues"]),
    }


def inventory(root: Path, paths: list[str], require_primary: bool = True) -> dict:
    root = root.resolve()
    report: dict = {
        "schema_version": 1,
        "primary_text_source": PRIMARY_TEXT,
        "coverage": {
            "runtime_load_order": "not_resolved",
            "sql_definitions": "not_evaluated; language mentions and literal candidates only",
            "code": "lexical candidates; not an AST, call graph, or data-flow analysis",
            "dynamic_keys": "not_resolved",
            "xml_fields": "language operations, key tokens, and selected UI/data text fields",
            "assets": "listed_for_manual_review; embedded text is not extracted",
            "translation_coverage": "not_computed; base-game and runtime databases are needed",
        },
        "files": [], "language_blocks": [], "operations": [], "literals": [],
        "sql_language_mentions": [], "encoding_notes": [], "issues": [],
    }
    for relative in sorted(set(paths)):
        path = root / relative
        record = {"path": relative, "scope": scope_for(relative), "status": "not_scanned"}
        report["files"].append(record)
        if not path.resolve().is_relative_to(root):
            record["status"] = "outside_repository"
            add_issue(report, "error", relative, 1, "outside_repository", "Refusing to read a source outside the repository")
            continue
        try:
            record["bytes"] = path.stat().st_size
            if not path.is_file():
                record["status"] = "directory_or_submodule"
                add_issue(report, "warning", relative, 1, "not_a_file", "Directory/submodule needs a separate inventory")
                continue
            if Path(relative).parts[0] not in SOURCE_ROOTS:
                record["status"] = "repository_support_file"
                continue
            extension = effective_extension(relative)
            if extension not in CODE_EXTENSIONS | {".xml", ".modinfo", ".civ5pkg"}:
                record["status"] = "asset_manual_review" if extension in ASSET_EXTENSIONS else "other_format_manual_review"
                continue
            data = path.read_bytes()
            record["sha256"] = hashlib.sha256(data).hexdigest()
            if extension in {".xml", ".modinfo", ".civ5pkg"}:
                if not data.strip():
                    record["status"] = "empty_override_stub" if record["scope"] == "override" else "empty_xml"
                    if relative == PRIMARY_TEXT or record["scope"] != "override":
                        add_issue(report, "error", relative, 1, "empty_xml", "Expected a nonempty XML document")
                else:
                    record["status"] = scan_xml(report, relative, data)
            else:
                record["status"] = scan_code(report, relative, data, extension)
        except OSError as error:
            record["status"] = "read_error"
            add_issue(report, "error", relative, 1, "read_error", str(error))
    if require_primary and not any(
        op["path"] == PRIMARY_TEXT and op["table"] == "Language_en_US"
        and op["operation"] in {"Row", "Replace"} and "Text" in op["fields"]
        for op in report["operations"]
    ):
        add_issue(report, "error", PRIMARY_TEXT, 1, "missing_primary_text", "No English text definitions found in the primary text file")
    report["summary"] = summarize(report)
    return report


def print_summary(report: dict) -> None:
    summary = report["summary"]
    print(f"Inventoried files: {summary['files']}")
    print("XML writes by source area (not effective runtime strings):")
    print(f"{'Scope':<13} {'Table':<24} {'Row':>7} {'Replace':>8} {'Update':>7} {'Delete':>7} {'Keys*':>7}")
    for table in summary["language_tables"]:
        ops = table["operations"]
        print(f"{table['scope']:<13} {table['table']:<24} {ops.get('Row', 0):>7} "
              f"{ops.get('Replace', 0):>8} {ops.get('Update', 0):>7} "
              f"{ops.get('Delete', 0):>7} {table['distinct_write_selectors']:>7}")
    print("* Distinct write selectors, including metadata updates; SQL is not evaluated.")
    print(f"Repeated write targets to review: {summary['repeated_write_targets']} (not automatically errors)")
    print(
        "Keys written in more than one source area: "
        f"{summary['cross_source_write_targets']} "
        f"({summary['cross_source_text_differences']} with different text)"
    )
    print(
        "English Art keys absent from Override: "
        f"{summary['art_english_keys_not_in_override']}"
    )
    print(f"Source/UI string candidates: {summary['string_candidates']}")
    print(f"  containing TXT_KEY references: {summary['key_reference_candidates']}")
    print(f"  other strings for review: {summary['non_key_string_candidates']}")
    print(f"SQL files with language-table mentions: {summary['sql_files_with_language_mentions']}")
    print(f"Sources with non-UTF-8 bytes: {len(report['encoding_notes'])} (bytes preserved; see JSON)")
    print("File coverage:")
    for status, count in summary["file_statuses"].items():
        print(f"  {status}: {count}")
    print(f"Issues: {summary['errors']} errors, {summary['warnings']} warnings")
    for issue in report["issues"][:20]:
        print(f"  {issue['severity']}: {issue['path']}:{issue['line']}: {issue['code']}: {issue['message']}")
    if len(report["issues"]) > 20:
        print(f"  ... {len(report['issues']) - 20} more issues; use --json for the complete list.")
    print("Coverage is partial: code use, dynamic keys, SQL effects, assets and load order require review.")


def print_key(report: dict, key: str) -> None:
    print(f"\nOccurrences of {key}:")
    found = False
    for op in report["operations"]:
        if op["key"] == key or op["fields"].get("Tag") == key:
            found = True
            print(f"  {op['path']}:{op['line']} [{op['table']} {op['operation']}]")
            # repr makes leading/trailing whitespace and metadata-only writes visible.
            print(f"    fields={op['fields']!r} where={op['where']!r}")
    for literal in report["literals"]:
        if key in literal["key_tokens"]:
            found = True
            print(f"  {literal['path']}:{literal['line']} [{literal['kind']}] {literal['value']!r}")
    if not found:
        print("  No exact token found. This does not rule out dynamic construction or external definitions.")


def write_report(destination: Path, report: dict, root: Path, inputs: list[str]) -> None:
    destination = destination.resolve()
    if destination.suffix.lower() != ".json":
        raise ValueError("The report path must end in .json")
    if destination in {(root / p).resolve() for p in inputs}:
        raise ValueError("The report must not overwrite a repository input; use build/localization/inventory.json")
    if destination.is_relative_to(root):
        relative = destination.relative_to(root)
        if relative.parts[0] in SOURCE_ROOTS:
            raise ValueError("Reports must be outside game, DLL, map and installer source directories")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=destination.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Git working tree to inventory")
    parser.add_argument("--json", type=Path, metavar="PATH", help="Write the complete inventory to a new JSON report")
    parser.add_argument("--key", metavar="TXT_KEY", help="Show source operations and literal candidates for one key")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 for unreadable inputs or invalid/unsupported data XML; "
            "review findings remain non-blocking"
        ),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        paths = git_files(root)
        report = inventory(root, paths)
        report["revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        print_summary(report)
        if args.key:
            print_key(report, args.key)
        if args.json:
            write_report(args.json, report, root, paths)
            print(f"JSON report: {args.json}")
        return 1 if args.strict and report["summary"]["errors"] else 0
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Inventory failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
