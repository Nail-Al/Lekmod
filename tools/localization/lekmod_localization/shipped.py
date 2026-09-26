"""Build the generated non-English section in the loaded Override XML."""

from __future__ import annotations

import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .common import CatalogError, PLACEHOLDER_RE, SOURCE_LOCALE, token_counts
from .fallback import fallback_entries
from .workspace import editor_source_fingerprint


BEGIN = "\t<!-- BEGIN GENERATED FALLBACK -->\n"
END = "\t<!-- END GENERATED FALLBACK -->\n"
EMPTY_LANGUAGE = re.compile(
    r"(?m)^[ \t]*<Language_([A-Za-z0-9_]+)>[ \t]*\n"
    r"[ \t]*</Language_\1>[ \t]*\n"
)


def read_approvals(path: Path) -> dict:
    """Require explicit, pinned approval; a draft CSV is never shipped."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CatalogError(f"cannot read approved translations: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1 or (
        not isinstance(data.get("translations"), dict)
    ):
        raise CatalogError("unsupported approved translations schema")
    return data["translations"]


def approved_entries(
    catalog: dict, locales: list[str], approvals: dict,
) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, int]]:
    """Overlay only reviewed texts matching the current English fingerprint."""
    fallback = fallback_entries(catalog)
    source = {
        key: entry
        for subcategories in catalog["source_categories"].values()
        for entries in subcategories.values()
        for key, entry in entries.items()
        if key in fallback
    }
    targets = {locale.casefold(): locale for locale in locales}
    if len(targets) != len(locales) or SOURCE_LOCALE.casefold() in targets:
        raise CatalogError("invalid or duplicate target locales")
    if not isinstance(approvals, dict):
        raise CatalogError("approved translations must be a locale map")
    result = {locale: {key: fields.copy() for key, fields in fallback.items()}
              for locale in locales}
    counts = {locale: 0 for locale in locales}
    seen = set()
    for selected_locale, translated in approvals.items():
        locale = targets.get(selected_locale.casefold()) if isinstance(selected_locale, str) else None
        if locale is None or locale in seen or not isinstance(translated, dict):
            raise CatalogError(f"invalid approved locale: {selected_locale}")
        seen.add(locale)
        for key, approval in translated.items():
            if key not in source or not isinstance(approval, dict):
                raise CatalogError(f"unknown approved key: {locale} {key}")
            if approval.get("source_fingerprint") != editor_source_fingerprint(
                key, source[key]
            ):
                raise CatalogError(f"stale approved translation: {locale} {key}")
            text = approval.get("text")
            if not isinstance(text, str) or (not text and fallback[key]["Text"]):
                raise CatalogError(f"empty approved translation: {locale} {key}")
            if PLACEHOLDER_RE.search(text) or (
                token_counts(text) != source[key]["lekmod_en_US"]["format_tokens"]
            ):
                raise CatalogError(f"invalid translation tokens: {locale} {key}")
            if set(approval) - {"source_fingerprint", "text", "gender", "plurality"}:
                raise CatalogError(f"unknown approval fields: {locale} {key}")
            fields = result[locale][key]
            fields["Text"] = text
            for name in ("gender", "plurality"):
                if name in approval:
                    value = approval[name]
                    if not isinstance(value, str):
                        raise CatalogError(f"invalid approval metadata: {locale} {key}")
                    fields[name.capitalize()] = value
            counts[locale] += 1
    return result, counts


def render_blocks(entries_by_locale: dict[str, dict[str, dict[str, str]]]) -> str:
    """Render deterministic Replace rows for each target language."""
    blocks = []
    for locale, entries in sorted(entries_by_locale.items()):
        language = ET.Element(f"Language_{locale}")
        for key, fields in sorted(entries.items()):
            operation = ET.SubElement(language, "Replace", {"Tag": key})
            for name in ("Text", "Gender", "Plurality"):
                if name in fields:
                    ET.SubElement(operation, name).text = fields[name]
        ET.indent(language, space="  ")
        blocks.append("\t" + ET.tostring(language, encoding="unicode") + "\n")
    return "".join(blocks)


def install_candidate(document: str, blocks: str, locales: list[str]) -> str:
    """Replace only our marked section; consume empty legacy locale stubs once."""
    if document.count(BEGIN) != document.count(END) or document.count(BEGIN) > 1:
        raise CatalogError("generated fallback markers are missing or duplicated")
    if BEGIN in document:
        start = document.index(BEGIN)
        finish = document.index(END)
        if finish <= start:
            raise CatalogError("generated fallback markers are out of order")
        document = document[:start] + document[finish + len(END):]
    else:
        targets = {locale.casefold() for locale in locales}
        document = EMPTY_LANGUAGE.sub(
            lambda match: "" if match.group(1).casefold() in targets else match.group(),
            document,
        )
        try:
            root = ET.fromstring(document)
        except ET.ParseError as error:
            raise CatalogError(f"invalid game XML: {error}") from error
        existing = {
            element.tag[len("Language_"):].casefold()
            for element in root if element.tag.startswith("Language_")
        }
        if existing & targets:
            raise CatalogError("game XML has unmarked nonempty target language blocks")
    match = re.search(r"(?m)^</GameData>[ \t]*$", document)
    if match is None:
        raise CatalogError("game XML has no final GameData close tag")
    candidate = document[:match.start()] + BEGIN + blocks + END + document[match.start():]
    try:
        ET.fromstring(candidate)
    except ET.ParseError as error:
        raise CatalogError(f"generated game XML is invalid: {error}") from error
    return candidate
