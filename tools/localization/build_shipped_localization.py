"""Generate checked non-English fallback inside Lekmod's loaded game XML."""

from __future__ import annotations

import argparse
from pathlib import Path

import audit_primary_localization as primary_audit
import sync_primary_english
from lekmod_localization.catalog import build_catalog
from lekmod_localization.common import (
    CatalogError, DEFAULT_ART_ROOT, DEFAULT_SOURCE, REPO_ROOT, SOURCE_LOCALE,
)
from lekmod_localization.shipped import (
    approved_entries, install_candidate, read_approvals, render_blocks,
)
from lekmod_localization.sources import load_repository_localizations
from lekmod_localization.vanilla_snapshot import read_snapshot


DEFAULT_APPROVALS = REPO_ROOT / "localization" / "approved-translations.json"


def build_candidate(
    snapshot: Path, source: Path = DEFAULT_SOURCE,
    english_source: Path = sync_primary_english.DEFAULT_ENGLISH,
    art_root: Path = DEFAULT_ART_ROOT,
    approvals: Path = DEFAULT_APPROVALS,
    root: Path = REPO_ROOT,
) -> tuple[str, dict]:
    """Recalculate from current inputs rather than trusting an old catalog."""
    sync_primary_english.synchronize(english_source, source, write=False)
    vanilla, fingerprints = read_snapshot(snapshot)
    english_locale = next(
        locale for locale in vanilla if locale.casefold() == SOURCE_LOCALE.casefold()
    )
    locales = sorted(locale for locale in vanilla if locale != english_locale)
    report = primary_audit.parse_source(source, SOURCE_LOCALE)
    localizations, references, summary = load_repository_localizations(
        source, art_root, root,
    )
    catalog = build_catalog(
        report, vanilla[english_locale], sorted(vanilla),
        snapshot.name, fingerprints[english_locale],
        localizations, references, summary,
    )
    entries, counts = approved_entries(
        catalog, locales, read_approvals(approvals),
    )
    document = source.read_text(encoding="utf-8")
    candidate = install_candidate(document, render_blocks(entries), locales)
    return candidate, {
        "locales": len(locales),
        "entries_per_locale": catalog["summary"]["requires_translation"],
        "conflicts_withheld": catalog["summary"]["requires_source_review"],
        "approved_translations": sum(counts.values()),
    }


def main() -> int:
    """Dry-run, write, or check the game's generated language blocks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vanilla-snapshot", type=Path, required=True)
    parser.add_argument("--approved", type=Path, default=DEFAULT_APPROVALS)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help="Update the shipped XML")
    action.add_argument("--check", action="store_true", help="Verify the shipped XML")
    args = parser.parse_args()
    try:
        candidate, summary = build_candidate(
            args.vanilla_snapshot, approvals=args.approved,
        )
        current = DEFAULT_SOURCE.read_text(encoding="utf-8")
        if args.write and candidate != current:
            sync_primary_english.atomic_text(DEFAULT_SOURCE, candidate)
        elif args.check and candidate != current:
            raise CatalogError("shipped localization is out of sync; run --write")
    except (CatalogError, OSError, UnicodeError) as error:
        parser.exit(1, f"Shipped localization failed: {error}\n")
    print(
        f"{summary['entries_per_locale']} rows in {summary['locales']} locales; "
        f"{summary['approved_translations']} approved translations; "
        f"{summary['conflicts_withheld']} English source conflicts withheld."
    )
    print("Shipped XML written." if args.write else
          "Shipped XML matches." if args.check else
          "Dry run successful; use --write to update shipped XML.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
