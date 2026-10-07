"""Shared paths, normalization rules, and safety primitives."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys

from .connections import project_root


# A packaged editor keeps writable project data beside the executable, not in
# PyInstaller's temporary extraction directory.
# IDE commands operate on their checkout. Only the browser editor follows its
# saved connection; it must not silently redirect a maintainer's CLI commands.
REPO_ROOT = project_root(editor=getattr(sys, 'frozen', False) or
                         Path(sys.argv[0]).stem in ('editor_server', 'editor_main'))
DEFAULT_SOURCE = (
    REPO_ROOT / "LEKMOD" / "Override" / "CIV5Units_Mongol.xml"
)
DEFAULT_ART_ROOT = REPO_ROOT / "LEKMOD" / "Art"
WORKSPACE = REPO_ROOT / "localization" / "workspace"
DEFAULT_OUTPUT = WORKSPACE / "catalog.json"
DEFAULT_REVIEW_OUTPUT = WORKSPACE / "review"
DEFAULT_EDITOR_OUTPUT = WORKSPACE / "editor"
SOURCE_LOCALE = "en_US"
TRANSLATABLE_FIELDS = ("Text", "Gender", "Plurality")

BRACKET_TOKEN_RE = re.compile(r"\[[^\[\]]+\]")
# Civ markup uses uppercase names, including parameterized colors and links.
# Footnotes and bracketed prose are ordinary text, not formatting instructions.
FORMAT_BRACKET_RE = re.compile(r"\[(?:[A-Z][A-Z0-9_]*|COLOR:[0-9:]+|LINK=[^\[\]]+|[/\\][A-Z][A-Z0-9_]*)\]")
BRACE_TOKEN_RE = re.compile(r"\{[^{}]+\}")
PRINTF_TOKEN_RE = re.compile(r"%(?:\d+\$)?[a-zA-Z%]")
KEY_RE = re.compile(r"^TXT_KEY_[A-Za-z0-9_]+$")
LANGUAGE_RE = re.compile(r"^Language_([A-Za-z0-9_]+)$")
PLACEHOLDER_RE = re.compile(r"\([A-Za-z_]+ text\)", re.IGNORECASE)


class CatalogError(ValueError):
    """Raised when localization inputs are unsafe or inconsistent."""

    pass


def local_name(tag: object) -> str:
    """Strip an XML namespace from a tag before comparison."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier without allowing SQL injection."""
    return '"' + identifier.replace('"', '""') + '"'


def canonical_fields(fields: dict[str, object]) -> dict[str, str | None]:
    """Keep supported text and grammar fields in canonical casing."""
    result: dict[str, str | None] = {}

    for name in TRANSLATABLE_FIELDS:
        if name in fields:
            value = fields[name]
            result[name] = None if value is None else str(value)

    return result


def normalize_text(value: object) -> str | None:
    """Collapse whitespace consistently for source comparison."""
    if value is None:
        return None
    return " ".join(str(value).split())


def normalize_metadata(value: object) -> str | None:
    """Normalize grammar metadata, treating blank values as absent."""
    if value is None:
        return None

    normalized = " ".join(str(value).split())
    return normalized or None


def catalog_fields(fields: dict[str, object]) -> dict[str, str | None]:
    """Normalize supported fields while preserving which were set."""
    result = canonical_fields(fields)

    if "Text" in result:
        result["Text"] = normalize_text(result["Text"])

    for name in ("Gender", "Plurality"):
        if name in result:
            result[name] = normalize_metadata(result[name])

    return result


def normalized_fields(fields: dict[str, object]) -> dict[str, str | None]:
    """Fill absent supported fields for stable variant comparisons."""
    result: dict[str, str | None] = {}

    for name in TRANSLATABLE_FIELDS:
        value = fields.get(name)
        if name == "Text":
            value = normalize_text(value)
        elif name in {"Gender", "Plurality"}:
            value = normalize_metadata(value)
        result[name] = None if value is None else str(value)

    return result


def character_count(text: str | None) -> int | None:
    """Count normalized source characters for translator context."""
    normalized = normalize_text(text)
    return None if normalized is None else len(normalized)


def token_counts(text: str | None) -> dict[str, int]:
    """Count formatting tokens a translation must preserve."""
    if not text:
        return {}

    tokens = (
        [token for token in BRACKET_TOKEN_RE.findall(text) if FORMAT_BRACKET_RE.fullmatch(token)]
        + BRACE_TOKEN_RE.findall(text)
        + PRINTF_TOKEN_RE.findall(text)
    )
    return dict(sorted(Counter(tokens).items()))


def literal_bracket_counts(text: str | None) -> dict[str, int]:
    """Retain v0.25 source hashes without requiring prose/footnotes in translations."""
    return dict(Counter(token for token in BRACKET_TOKEN_RE.findall(text or '')
                        if not FORMAT_BRACKET_RE.fullmatch(token)))


def token_difference(text: str | None, required: dict[str, int]) -> tuple[dict[str, int], dict[str, int]]:
    """Preserve source tokens, allow additional icons, ignore legacy prose tokens."""
    actual = Counter(token_counts(text))
    expected = Counter({name: amount for name, amount in required.items()
                        if not BRACKET_TOKEN_RE.fullmatch(name) or FORMAT_BRACKET_RE.fullmatch(name)})
    missing = dict(expected - actual)
    unexpected = {name: amount for name, amount in (actual - expected).items()
                  if not re.fullmatch(r'\[ICON_[A-Z0-9_]+\]', name)}
    return missing, unexpected


def tokens_match(text: str | None, required: dict[str, int]) -> bool:
    """Use the same formatting rule for drafts, builds, exports and imports."""
    return not any(token_difference(text, required))


def canonical_locale(locale: str, available: list[str]) -> str:
    """Return the database spelling for a case-insensitive locale name."""
    match = next(
        (
            name
            for name in available
            if name.casefold() == locale.casefold()
        ),
        None,
    )
    if match is None:
        raise CatalogError(
            f"Language_{locale} was not found; available locales: "
            + ", ".join(sorted(available, key=str.casefold))
        )
    return match


def coalesce_variants(
    variants: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Combine equivalent definitions and retain their source paths."""
    grouped: dict[str, dict[str, object]] = {}

    for variant in variants:
        fields = catalog_fields(dict(variant.get("fields", {})))
        identity = json.dumps(
            normalized_fields(fields),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity not in grouped:
            grouped[identity] = {
                "fields": fields,
                "sources": [],
            }
        grouped[identity]["sources"].extend(
            variant.get("sources", [])
        )

    result = []
    for identity in sorted(grouped):
        variant = grouped[identity]
        variant["sources"] = sorted(set(variant["sources"]))
        result.append(variant)
    return result
