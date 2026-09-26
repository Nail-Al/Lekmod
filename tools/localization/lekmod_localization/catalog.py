"""Build the language-independent catalog and per-locale review data."""

from __future__ import annotations

from collections import Counter, defaultdict

from .common import (
    CatalogError,
    PLACEHOLDER_RE,
    SOURCE_LOCALE,
    catalog_fields,
    character_count,
    coalesce_variants,
    normalize_metadata,
    normalize_text,
    token_counts,
)
from .sources import locale_entries
from .taxonomy import classify_context


def text_variant(
    fields: dict[str, object],
    sources: list[str] | None = None,
) -> dict[str, object]:
    """Normalize one source variant for catalog and review output."""
    normalized = catalog_fields(fields)
    text = normalized.get("Text")
    return {
        "text": text,
        "characters": character_count(text),
        "gender": normalized.get("Gender"),
        "plurality": normalized.get("Plurality"),
        "fields_present": sorted(normalized),
        "format_tokens": token_counts(text),
        "sources": sorted(set(sources or [])),
    }


def source_text_record(
    variants: list[dict[str, object]],
) -> dict[str, object]:
    """Merge identical definitions or flag conflicting source variants."""
    compact = coalesce_variants(variants)
    if not compact:
        return {
            "status": "missing",
            "text": None,
            "characters": None,
            "gender": None,
            "plurality": None,
            "fields_present": [],
            "format_tokens": {},
            "sources": [],
        }
    if len(compact) == 1:
        variant = compact[0]
        return {
            "status": "present",
            **text_variant(
                variant["fields"],
                variant["sources"],
            ),
        }
    return {
        "status": "source_conflict",
        "text": None,
        "characters": None,
        "gender": None,
        "plurality": None,
        "fields_present": [],
        "format_tokens": {},
        "sources": sorted({
            source
            for variant in compact
            for source in variant["sources"]
        }),
        "variants": [
            text_variant(
                variant["fields"],
                variant["sources"],
            )
            for variant in compact
        ],
    }


def official_game_record(
    key: str,
    locale: str,
    vanilla_english: dict[str, dict[str, object]],
    vanilla_target: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Describe an official text key in one vanilla locale."""
    if key not in vanilla_english:
        return {
            "locale": locale,
            "status": "not_in_vanilla",
            "text": None,
            "characters": None,
            "gender": None,
            "plurality": None,
            "fields_present": [],
            "format_tokens": {},
        }
    if key not in vanilla_target:
        return {
            "locale": locale,
            "status": "missing_in_vanilla_locale",
            "text": None,
            "characters": None,
            "gender": None,
            "plurality": None,
            "fields_present": [],
            "format_tokens": {},
        }
    return {
        "locale": locale,
        "status": "present",
        **{
            name: value
            for name, value in text_variant(
                vanilla_target[key]
            ).items()
            if name not in {"sources", "fields_present"}
        },
    }


def target_text_record(
    locale: str,
    variants: list[dict[str, object]],
    english: dict[str, object],
) -> dict[str, object]:
    """Classify one locale's existing Lekmod text or placeholder."""
    record = source_text_record(variants)
    record["locale"] = locale
    if record["status"] == "present":
        text = record.get("text")
        record["status"] = (
            "placeholder"
            if isinstance(text, str) and PLACEHOLDER_RE.search(text)
            else "present"
        )
        record["same_as_english"] = (
            normalize_text(text) is not None
            and normalize_text(text)
            == normalize_text(english.get("text"))
        )
    return record


def explicit_metadata_changed(
    source_fields: dict[str, object],
    vanilla_fields: dict[str, object],
) -> bool:
    """Detect an explicit grammar change relative to vanilla."""
    for name in ("Gender", "Plurality"):
        if name not in source_fields:
            continue

        source_value = normalize_metadata(source_fields.get(name))
        vanilla_value = normalize_metadata(vanilla_fields.get(name))

        if source_value != vanilla_value:
            return True

    return False


def source_variants_from_report(
    source_report: dict,
) -> dict[str, list[dict[str, object]]]:
    """Adapt the primary-only audit to the repository variant shape."""
    return {
        key: [{
            "fields": catalog_fields(fields),
            "sources": [source_report["source"]],
        }]
        for key, fields in source_report["entries"].items()
    }


def build_catalog(
    source_report: dict,
    vanilla_entries: dict[str, dict[str, object]],
    vanilla_locales: list[str],
    vanilla_database_name: str,
    vanilla_fingerprint: str,
    repository_localizations: (
        dict[str, dict[str, list[dict[str, object]]]] | None
    ) = None,
    references: dict[str, list[dict[str, str]]] | None = None,
    repository_summary: dict[str, object] | None = None,
) -> dict:
    """Classify English keys against frozen vanilla and group by context."""
    if source_report["summary"]["errors"]:
        raise CatalogError(
            "primary localization source contains audit errors"
        )

    if repository_localizations is None:
        english_variants = source_variants_from_report(source_report)
    else:
        english_variants = locale_entries(
            repository_localizations,
            SOURCE_LOCALE,
        )

    references = references or {}
    classification_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    subcategory_counts: dict[
        str, Counter[str]
    ] = defaultdict(Counter)
    categories: dict[
        str, dict[str, dict[str, dict[str, object]]]
    ] = defaultdict(lambda: defaultdict(dict))
    metadata_review: dict[str, dict[str, object]] = {}

    for key in sorted(english_variants):
        english = source_text_record(english_variants[key])
        vanilla_fields = vanilla_entries.get(key)

        if english["status"] == "source_conflict":
            classification = "source_conflict"
        else:
            field_values = {
                "Text": english.get("text"),
                "Gender": english.get("gender"),
                "Plurality": english.get("plurality"),
            }
            source_fields = {
                name: field_values[name]
                for name in english.get("fields_present", [])
            }
            if vanilla_fields is None:
                classification = "lekmod_new"
            elif "Text" in source_fields and (
                source_fields.get("Text")
                != vanilla_fields.get("Text")
            ):
                classification = "vanilla_modified"
            elif explicit_metadata_changed(
                source_fields,
                vanilla_fields,
            ):
                classification = "vanilla_metadata_only"
            else:
                classification = "vanilla_unchanged"

        classification_counts[classification] += 1
        context = references.get(key, [])
        category, subcategory, context_source = classify_context(
            key,
            context,
        )

        if classification in {
            "vanilla_unchanged",
            "vanilla_metadata_only",
        }:
            if classification == "vanilla_metadata_only":
                metadata_review[key] = {
                    "category": category,
                    "subcategory": subcategory,
                    "lekmod_en_US": english,
                }
            continue

        category_counts[category] += 1
        subcategory_counts[category][subcategory] += 1
        categories[category][subcategory][key] = {
            "classification": classification,
            "context_source": context_source,
            "contexts": context,
            "lekmod_en_US": english,
        }

    ordered_categories = {
        category: {
            subcategory: dict(sorted(entries.items()))
            for subcategory, entries in sorted(
                categories[category].items()
            )
        }
        for category in sorted(categories)
    }
    target_locales = [
        locale
        for locale in vanilla_locales
        if locale.casefold() != SOURCE_LOCALE.casefold()
    ]
    ready = (
        classification_counts["lekmod_new"]
        + classification_counts["vanilla_modified"]
    )
    source_review = classification_counts["source_conflict"]

    return {
        "schema_version": 2,
        "source": {
            "file": source_report["source"],
            "sha256": source_report["source_sha256"],
            "locale": SOURCE_LOCALE,
            "primary_entries": source_report["summary"]["entries"],
            "combined_english_selectors": len(english_variants),
            "repository": repository_summary or {
                "xml_files": 1,
                "locales": [SOURCE_LOCALE],
            },
        },
        "vanilla": {
            "database": vanilla_database_name,
            "locale": SOURCE_LOCALE,
            "entries": len(vanilla_entries),
            "content_sha256": vanilla_fingerprint,
        },
        "locales": [SOURCE_LOCALE, *target_locales],
        "summary": {
            "classifications": dict(
                sorted(classification_counts.items())
            ),
            "requires_translation": ready,
            "requires_source_review": source_review,
            "workspace_entries": ready + source_review,
            "categories": dict(sorted(category_counts.items())),
            "subcategories": {
                category: dict(sorted(counts.items()))
                for category, counts in sorted(
                    subcategory_counts.items()
                )
            },
        },
        "source_categories": ordered_categories,
        "review": {
            "metadata_only": metadata_review,
        },
    }


def build_locale_review(
    catalog: dict,
    locale: str,
    vanilla_english: dict[str, dict[str, object]],
    vanilla_target: dict[str, dict[str, object]],
    repository_localizations: dict[
        str, dict[str, list[dict[str, object]]]
    ],
) -> tuple[dict[str, dict], dict[str, object]]:
    """Compare each eligible key with vanilla and Lekmod target text."""
    target_entries = locale_entries(
        repository_localizations,
        locale,
    )
    documents = {}
    status_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()

    for category, subcategories in (
        catalog["source_categories"].items()
    ):
        output_subcategories = {}
        category_statuses: Counter[str] = Counter()

        for subcategory, entries in subcategories.items():
            output_entries = {}
            for key, source_entry in entries.items():
                if (
                    source_entry["classification"]
                    == "source_conflict"
                ):
                    continue

                english = source_entry["lekmod_en_US"]
                target = target_text_record(
                    locale,
                    target_entries.get(key, []),
                    english,
                )
                official = official_game_record(
                    key,
                    locale,
                    vanilla_english,
                    vanilla_target,
                )
                official_english = official_game_record(
                    key,
                    SOURCE_LOCALE,
                    vanilla_english,
                    vanilla_english,
                )
                status_counts[target["status"]] += 1
                category_statuses[target["status"]] += 1
                classification_counts[
                    source_entry["classification"]
                ] += 1
                output_entries[key] = {
                    "classification": (
                        source_entry["classification"]
                    ),
                    "official_game_en_US": official_english,
                    "official_game": official,
                    "lekmod_en_US": english,
                    "lekmod_target": target,
                    "context_source": (
                        source_entry["context_source"]
                    ),
                    "contexts": source_entry["contexts"],
                }

            if output_entries:
                output_subcategories[subcategory] = output_entries

        if not output_subcategories:
            continue

        documents[category] = {
            "schema_version": 2,
            "locale": locale,
            "category": category,
            "summary": {
                "entries": sum(
                    len(entries)
                    for entries in output_subcategories.values()
                ),
                "target_status": dict(
                    sorted(category_statuses.items())
                ),
            },
            "subcategories": output_subcategories,
        }

    manifest = {
        "schema_version": 2,
        "locale": locale,
        "generated_from": "catalog.json",
        "summary": {
            "entries": sum(status_counts.values()),
            "target_status": dict(sorted(status_counts.items())),
            "classifications": dict(
                sorted(classification_counts.items())
            ),
            "categories": {
                category: document["summary"]["entries"]
                for category, document in sorted(documents.items())
            },
        },
        "files": [
            f"{category}.json"
            for category in sorted(documents)
        ],
    }
    return documents, manifest


def build_source_conflict_review(catalog: dict) -> dict:
    """Collect incompatible English variants for developer decisions."""
    categories: dict[
        str, dict[str, dict[str, dict[str, object]]]
    ] = {}
    category_counts: Counter[str] = Counter()
    subcategory_counts: dict[
        str, Counter[str]
    ] = defaultdict(Counter)

    for category, subcategories in (
        catalog["source_categories"].items()
    ):
        output_subcategories = {}

        for subcategory, entries in subcategories.items():
            output_entries = {}

            for key, entry in entries.items():
                if entry["classification"] != "source_conflict":
                    continue

                output_entries[key] = {
                    "context_source": entry["context_source"],
                    "contexts": entry["contexts"],
                    "lekmod_en_US": entry["lekmod_en_US"],
                }
                category_counts[category] += 1
                subcategory_counts[category][subcategory] += 1

            if output_entries:
                output_subcategories[subcategory] = output_entries

        if output_subcategories:
            categories[category] = output_subcategories

    return {
        "schema_version": 1,
        "status": "developer_review_required",
        "summary": {
            "entries": sum(category_counts.values()),
            "categories": dict(sorted(category_counts.items())),
            "subcategories": {
                category: dict(sorted(counts.items()))
                for category, counts in sorted(
                    subcategory_counts.items()
                )
            },
        },
        "categories": categories,
    }
