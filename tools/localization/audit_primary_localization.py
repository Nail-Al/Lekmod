from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / "LEKMOD" / "Override" / "CIV5Units_Mongol.xml"
SUPPORTED_OPERATIONS = {"Row", "Replace", "Update", "Delete"}


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def read_columns(element: ET.Element) -> tuple[dict[str, str], list[str]]:
    columns = dict(element.attrib)
    warnings: list[str] = []

    for child in element:
        name = local_name(child.tag)
        if list(child):
            raise ValueError(f"nested column is not supported: {name}")
        if name in columns:
            raise ValueError(f"duplicate column: {name}")
        columns[name] = "".join(child.itertext())

    for canonical in ("Tag", "Text", "Gender", "Plurality"):
        matches = [name for name in columns if name.lower() == canonical.lower()]
        if len(matches) > 1:
            raise ValueError(f"ambiguous {canonical} column: {', '.join(matches)}")
        if matches and matches[0] != canonical:
            original = matches[0]
            columns[canonical] = columns.pop(original)
            warnings.append(f"normalized column {original!r} to {canonical!r}")

    return columns, warnings


def parse_source(path: Path, locale: str) -> dict:
    issues: list[dict[str, object]] = []
    operations: list[dict[str, object]] = []

    try:
        data = path.read_bytes()
        root = ET.fromstring(data)
    except (OSError, ET.ParseError) as error:
        return {
            "schema_version": 1,
            "source": source_name(path),
            "locale": locale,
            "operations": [],
            "entries": {},
            "repeated_writes": {},
            "issues": [{"severity": "error", "operation": None, "message": str(error)}],
            "summary": {"operations": {}, "entries": 0, "repeated_writes": 0,
                        "errors": 1, "warnings": 0},
        }

    table_name = f"Language_{locale}"
    language_blocks = [
        element for element in root.iter() if local_name(element.tag) == table_name
    ]
    if not language_blocks:
        issues.append({
            "severity": "error",
            "operation": None,
            "message": f"{table_name} was not found",
        })

    for block_number, block in enumerate(language_blocks, 1):
        for block_index, element in enumerate(block):
            operation_name = local_name(element.tag)
            operation_number = len(operations)
            record: dict[str, object] = {
                "number": operation_number,
                "block": block_number,
                "block_index": block_index,
                "operation": operation_name,
                "key": None,
                "fields": {},
                "where": {},
            }

            try:
                if operation_name not in SUPPORTED_OPERATIONS:
                    raise ValueError(f"unsupported operation: {operation_name}")

                if operation_name in {"Row", "Replace", "Delete"}:
                    columns, column_warnings = read_columns(element)
                    target = "where" if operation_name == "Delete" else "fields"
                    record[target] = columns
                else:
                    children = defaultdict(list)
                    for child in element:
                        children[local_name(child.tag)].append(child)
                    if element.attrib or set(children) != {"Where", "Set"}:
                        raise ValueError("Update must contain one Where and one Set")
                    if len(children["Where"]) != 1 or len(children["Set"]) != 1:
                        raise ValueError("Update must contain exactly one Where and one Set")
                    where, where_warnings = read_columns(children["Where"][0])
                    fields, field_warnings = read_columns(children["Set"][0])
                    record["where"] = where
                    record["fields"] = fields
                    column_warnings = where_warnings + field_warnings

                selector = record["where"] if operation_name in {"Update", "Delete"} else record["fields"]
                key = selector.get("Tag")
                if not isinstance(key, str) or not key:
                    raise ValueError("operation has no nonempty Tag")
                record["key"] = key

                for message in column_warnings:
                    issues.append({
                        "severity": "warning",
                        "operation": operation_number,
                        "message": message,
                    })
            except ValueError as error:
                issues.append({
                    "severity": "error",
                    "operation": operation_number,
                    "message": str(error),
                })
                record["invalid"] = True

            operations.append(record)

    entries: dict[str, dict[str, str]] = {}
    write_numbers: dict[str, list[int]] = defaultdict(list)

    for operation in operations:
        if operation.get("invalid") or not operation["key"]:
            continue
        key = str(operation["key"])
        name = str(operation["operation"])

        if name in {"Row", "Replace"}:
            write_numbers[key].append(int(operation["number"]))
            entries[key] = dict(operation["fields"])
        elif name == "Update":
            write_numbers[key].append(int(operation["number"]))
            if key in entries:
                entries[key].update(operation["fields"])
        elif name == "Delete":
            entries.pop(key, None)

    repeated_writes = {
        key: numbers for key, numbers in sorted(write_numbers.items())
        if len(numbers) > 1
    }
    counts = Counter(str(operation["operation"]) for operation in operations)
    errors = sum(issue["severity"] == "error" for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)

    return {
        "schema_version": 1,
        "source": source_name(path),
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "locale": locale,
        "operations": operations,
        "entries": dict(sorted(entries.items())),
        "repeated_writes": repeated_writes,
        "issues": issues,
        "summary": {
            "operations": dict(sorted(counts.items())),
            "entries": len(entries),
            "repeated_writes": len(repeated_writes),
            "errors": errors,
            "warnings": warnings,
        },
    }


def source_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_report(path: Path, report: dict, source: Path) -> None:
    destination = path.resolve()
    if destination.suffix.lower() != ".json":
        raise ValueError("report path must end in .json")
    if destination == source.resolve():
        raise ValueError("report cannot overwrite the source XML")
    if destination.is_relative_to(REPO_ROOT / "LEKMOD"):
        raise ValueError("report must be written outside the shipped LEKMOD files")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n",
        dir=destination.parent, suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit the primary English localization XML used by Lekmod."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--locale", default="en_US")
    parser.add_argument("--json", type=Path, metavar="PATH")
    parser.add_argument("--key", metavar="TXT_KEY")
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit with status 1 when the source cannot be parsed or contains unsupported operations.",
    )
    args = parser.parse_args()

    report = parse_source(args.source.resolve(), args.locale)
    summary = report["summary"]
    operation_text = ", ".join(
        f"{name}: {count}" for name, count in summary["operations"].items()
    )

    print(f"Source: {args.source}")
    print(f"Locale: Language_{args.locale}")
    print(f"Operations: {operation_text or 'none'}")
    print(f"Collected entries after source-local operation order: {summary['entries']}")
    print(f"Repeated write targets: {summary['repeated_writes']}")
    print(f"Issues: {summary['errors']} errors, {summary['warnings']} warnings")

    for issue in report["issues"]:
        location = "source" if issue["operation"] is None else f"operation {issue['operation']}"
        print(f"  {issue['severity']}: {location}: {issue['message']}")

    if args.key:
        matches = [operation for operation in report["operations"] if operation["key"] == args.key]
        print(f"\n{args.key}: {len(matches)} operations")
        for operation in matches:
            print(
                f"  #{operation['number']} {operation['operation']} "
                f"fields={operation['fields']!r} where={operation['where']!r}"
            )

    if args.json:
        try:
            write_report(args.json, report, args.source)
        except (OSError, ValueError) as error:
            parser.exit(1, f"Cannot write report: {error}\n")
        print(f"JSON report: {args.json}")

    return 1 if args.strict and summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
