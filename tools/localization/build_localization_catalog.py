"""Command-line entry point for the Lekmod translation workspace builder."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

import audit_primary_localization as primary_audit
from lekmod_localization.catalog import (
    build_catalog,
    build_locale_review,
    build_source_conflict_review,
)
from lekmod_localization.common import (
    CatalogError,
    DEFAULT_ART_ROOT,
    DEFAULT_EDITOR_OUTPUT,
    DEFAULT_OUTPUT,
    DEFAULT_REVIEW_OUTPUT,
    DEFAULT_SOURCE,
    SOURCE_LOCALE,
    canonical_locale,
)
from lekmod_localization.sources import (
    load_repository_localizations,
    load_vanilla_database,
    load_vanilla_locales,
    locale_entries,
    parse_language_sql,
)
from lekmod_localization.taxonomy import classify_context
from lekmod_localization.vanilla_snapshot import read_snapshot
from lekmod_localization.workspace import (
    EDITOR_EDITABLE_FIELDS,
    EDITOR_FIELDNAMES,
    validate_generated_outputs,
    write_catalog,
    write_editor_workspace,
    write_review_workspace,
)


def create_parser() -> argparse.ArgumentParser:
    """Define the stable command-line interface in one place."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a categorized, per-locale Lekmod translation review "
            "workspace from repository sources and a clean Civilization V "
            "localization database."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
    )
    parser.add_argument(
        "--art-root",
        type=Path,
        default=DEFAULT_ART_ROOT,
    )
    vanilla = parser.add_mutually_exclusive_group(required=True)
    vanilla.add_argument(
        "--vanilla-db",
        type=Path,
        help="Path to a clean Civilization V Localization-Merged.db.",
    )
    vanilla.add_argument(
        "--vanilla-snapshot",
        type=Path,
        help="Previously frozen, local vanilla-snapshot.json.gz.",
    )
    parser.add_argument(
        "--locale",
        action="append",
        metavar="LOCALE",
        help=(
            "Target locale to generate. Repeat for multiple locales; "
            "the default is every non-English locale in the clean database."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=DEFAULT_REVIEW_OUTPUT,
    )
    parser.add_argument(
        "--editor-output",
        type=Path,
        default=DEFAULT_EDITOR_OUTPUT,
        help=(
            "Directory for editable per-locale CSV files. Existing "
            "translation fields are preserved when sources are unchanged."
        ),
    )
    return parser


def select_locales(
    requested: list[str] | None,
    available: list[str],
) -> tuple[str, list[str]]:
    """Resolve English and target locale names from database tables."""
    english = canonical_locale(SOURCE_LOCALE, available)
    targets = (
        [canonical_locale(locale, available) for locale in requested]
        if requested
        else [
            locale
            for locale in available
            if locale.casefold() != SOURCE_LOCALE.casefold()
        ]
    )
    targets = list(dict.fromkeys(targets))
    if any(
        locale.casefold() == SOURCE_LOCALE.casefold()
        for locale in targets
    ):
        raise CatalogError(
            "--locale must select a non-English target locale"
        )
    return english, targets


def print_summary(
    catalog: dict,
    target_count: int,
    args: argparse.Namespace,
) -> None:
    """Print the compact report used by local runs and documentation."""
    classifications = catalog["summary"]["classifications"]
    print(
        "Primary English entries: "
        f"{catalog['source']['primary_entries']}"
    )
    print(
        "Combined English selectors: "
        f"{catalog['source']['combined_english_selectors']}"
    )
    print(
        "Vanilla English entries: "
        f"{catalog['vanilla']['entries']}"
    )
    for label, classification in (
        ("Vanilla unchanged", "vanilla_unchanged"),
        ("Vanilla metadata-only changes", "vanilla_metadata_only"),
        ("Vanilla modified", "vanilla_modified"),
        ("Lekmod new", "lekmod_new"),
        ("Source conflicts", "source_conflict"),
    ):
        print(f"{label}: {classifications.get(classification, 0)}")
    print(
        "Entries ready for translation: "
        f"{catalog['summary']['requires_translation']}"
    )
    print(f"Target locales generated: {target_count}")
    print(f"Catalog: {args.output}")
    print(f"Review workspace: {args.review_output}")
    print(f"Editor workspace: {args.editor_output}")


def main() -> int:
    """Build the categorized catalog, review JSON, and editor CSV files."""
    parser = create_parser()
    args = parser.parse_args()

    source = args.source.resolve()
    art_root = args.art_root.resolve()
    vanilla_input = (args.vanilla_db or args.vanilla_snapshot).resolve()
    source_report = primary_audit.parse_source(
        source,
        SOURCE_LOCALE,
    )

    try:
        validate_generated_outputs(
            args.output,
            args.review_output,
            args.editor_output,
        )
        all_vanilla, fingerprints = (
            read_snapshot(vanilla_input)
            if args.vanilla_snapshot
            else load_vanilla_locales(vanilla_input)
        )
        available = sorted(all_vanilla, key=str.casefold)
        english_locale, target_locales = select_locales(
            args.locale,
            available,
        )

        localizations, references, repository_summary = (
            load_repository_localizations(
                source,
                art_root,
            )
        )
        catalog = build_catalog(
            source_report,
            all_vanilla[english_locale],
            [english_locale, *target_locales],
            vanilla_input.name,
            fingerprints[english_locale],
            localizations,
            references,
            repository_summary,
        )
        source_conflicts = build_source_conflict_review(catalog)
        reviews = {
            locale: build_locale_review(
                catalog,
                locale,
                all_vanilla[english_locale],
                all_vanilla[locale],
                localizations,
            )
            for locale in target_locales
        }
        write_catalog(
            args.output,
            catalog,
            source,
            vanilla_input,
        )
        write_review_workspace(
            args.review_output,
            reviews,
            source,
            vanilla_input,
            source_conflicts,
        )
        write_editor_workspace(
            args.editor_output,
            reviews,
            source,
            vanilla_input,
        )
    except (CatalogError, OSError, sqlite3.Error) as error:
        parser.exit(
            1,
            f"Cannot build localization workspace: {error}\n",
        )

    print_summary(catalog, len(reviews), args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
