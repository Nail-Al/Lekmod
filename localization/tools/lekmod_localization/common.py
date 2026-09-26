"""Shared paths, normalization rules, and safety primitives."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[3]
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
        BRACKET_TOKEN_RE.findall(text)
        + BRACE_TOKEN_RE.findall(text)
        + PRINTF_TOKEN_RE.findall(text)
    )
    return dict(sorted(Counter(tokens).items()))


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
