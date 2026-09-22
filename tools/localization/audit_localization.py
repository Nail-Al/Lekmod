from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import re
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]
ART_ROOT = REPO_ROOT / "LEKMOD" / "Art"

LANGUAGE_TAG_RE = re.compile(r"^Language_([A-Za-z_]+)$")
LANGUAGE_REFERENCE_RE = re.compile(r"Language_([A-Za-z_]+)")
PLACEHOLDER_RE = re.compile(r"\([A-Za-z_]+ text\)", re.IGNORECASE)
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def child_named(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def operation_data(operation: ET.Element) -> tuple[str | None, str]:
    operation_type = local_name(operation.tag)

    if operation_type == "Row":
        key = operation.attrib.get("Tag")
        text_element = child_named(operation, "Text")
        text = "".join(text_element.itertext()) if text_element is not None else ""
        return key, text

    if operation_type == "Update":
        where_element = child_named(operation, "Where")
        set_element = child_named(operation, "Set")

        key = where_element.attrib.get("Tag") if where_element is not None else None
        text = ""

        if set_element is not None:
            text = set_element.attrib.get("Text", "")
            nested_text = child_named(set_element, "Text")
            if nested_text is not None:
                text = "".join(nested_text.itertext())

        return key, text

    if operation_type == "Delete":
        return operation.attrib.get("Tag"), ""

    return None, ""


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Lekmod localization tables."
    )
    parser.add_argument(
        "--locale",
        metavar="LOCALE",
        help=(
            "Print missing keys and duplicate write sources for one locale, "
            "for example RU_RU."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not ART_ROOT.is_dir():
        raise SystemExit(f"Art directory not found: {ART_ROOT}")

    stats: dict[str, Counter[str]] = defaultdict(Counter)
    files_by_locale: dict[str, set[str]] = defaultdict(set)
    written_keys: dict[str, set[str]] = defaultdict(set)
    write_entries: dict[
        str, dict[str, list[tuple[str, str]]]
    ] = defaultdict(lambda: defaultdict(list))
    parse_errors: list[tuple[str, str]] = []
    sql_references: dict[str, set[str]] = defaultdict(set)

    xml_files = sorted(ART_ROOT.rglob("*.xml"))

    for path in xml_files:
        relative_path = path.relative_to(REPO_ROOT).as_posix()

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError) as error:
            parse_errors.append((relative_path, str(error)))
            continue

        for element in root.iter():
            match = LANGUAGE_TAG_RE.match(local_name(element.tag))
            if not match:
                continue

            locale = match.group(1)
            stats[locale]["blocks"] += 1
            files_by_locale[locale].add(relative_path)

            for operation in element:
                operation_type = local_name(operation.tag)

                if operation_type not in {"Row", "Update", "Delete"}:
                    continue

                stats[locale]["operations"] += 1
                stats[locale][operation_type.lower()] += 1

                key, text = operation_data(operation)

                if operation_type in {"Row", "Update"} and key:
                    written_keys[locale].add(key)
                    write_entries[locale][key].append(
                        (relative_path, text)
                    )
                if text and PLACEHOLDER_RE.search(text):
                    stats[locale]["placeholders"] += 1

                if text and CYRILLIC_RE.search(text):
                    stats[locale]["cyrillic"] += 1

    for path in sorted(ART_ROOT.rglob("*.sql")):
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        content = path.read_text(encoding="utf-8-sig", errors="replace")

        for locale in LANGUAGE_REFERENCE_RE.findall(content):
            sql_references[locale].add(relative_path)

    if args.locale is not None and args.locale not in stats:
        available_locales = ", ".join(sorted(stats))
        raise SystemExit(
            f"Unknown locale: {args.locale}. "
            f"Available locales: {available_locales}"
        )

    print(f"Scanned XML files: {len(xml_files)}")
    print(f"XML parse errors: {len(parse_errors)}")
    print()

    header = (
        f"{'Locale':<14}"
        f"{'Files':>8}"
        f"{'Blocks':>9}"
        f"{'Rows':>8}"
        f"{'Updates':>10}"
        f"{'Deletes':>9}"
        f"{'Keys':>8}"
        f"{'Placeholders':>15}"
        f"{'Cyrillic':>11}"
    )
    print(header)
    print("-" * len(header))

    for locale in sorted(stats):
        locale_stats = stats[locale]
        print(
            f"{locale:<14}"
            f"{len(files_by_locale[locale]):>8}"
            f"{locale_stats['blocks']:>9}"
            f"{locale_stats['row']:>8}"
            f"{locale_stats['update']:>10}"
            f"{locale_stats['delete']:>9}"
            f"{len(written_keys[locale]):>8}"
            f"{locale_stats['placeholders']:>15}"
            f"{locale_stats['cyrillic']:>11}"
        )

    english_keys = written_keys.get("en_US", set())

    if english_keys:
        print()
        print("Missing keys compared with en_US:")

        for locale in sorted(written_keys):
            if locale == "en_US":
                continue

            missing = english_keys - written_keys[locale]
            print(f"  {locale}: {len(missing)}")

            if locale == args.locale:
                for key in sorted(missing):
                    print(f"    {key}")

    print()
    print("Duplicate writes:")

    for locale in sorted(write_entries):
        duplicates = {
            key: entries
            for key, entries in write_entries[locale].items()
            if len(entries) > 1
        }
        conflicts = {
            key
            for key, entries in duplicates.items()
            if len(
                {normalize_text(text) for _, text in entries}
            ) > 1
        }
        identical_count = len(duplicates) - len(conflicts)

        print(
            f"  {locale}: {len(duplicates)} total, "
            f"{len(conflicts)} conflicting, "
            f"{identical_count} identical"
        )

        if locale == args.locale:
            for key, entries in sorted(duplicates.items()):
                status = (
                    "conflicting"
                    if key in conflicts
                    else "identical"
                )
                print(f"    {key} [{status}]")

                for source, text in entries:
                    print(f"      {source}")
                    print(f"        {normalize_text(text)}")

    if sql_references:
        print()
        print("SQL files containing language-table references:")

        for locale in sorted(sql_references):
            print(f"  {locale}: {len(sql_references[locale])}")

    if parse_errors:
        print()
        print("Files with XML parse errors:")

        for path, error in parse_errors:
            print(f"  {path}: {error}")


if __name__ == "__main__":
    main()