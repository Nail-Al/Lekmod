"""Review English fallback for new and changed Lekmod keys in every locale."""

from __future__ import annotations

import argparse
from pathlib import Path

import audit_primary_localization as primary_audit
import sync_primary_english
from lekmod_localization.catalog import build_catalog
from lekmod_localization.common import (
    CatalogError, DEFAULT_ART_ROOT, DEFAULT_SOURCE, REPO_ROOT, SOURCE_LOCALE,
)
from lekmod_localization.fallback import preview_files, write_preview
from lekmod_localization.sources import load_repository_localizations
from lekmod_localization.vanilla_snapshot import read_snapshot


DEFAULT_PREVIEW = REPO_ROOT / "localization" / "workspace" / "fallback-preview"


def build_preview(
    snapshot: Path,
    output: Path,
    source: Path = DEFAULT_SOURCE,
    english_source: Path = sync_primary_english.DEFAULT_ENGLISH,
    art_root: Path = DEFAULT_ART_ROOT,
    root: Path = REPO_ROOT,
) -> dict:
    """Check live inputs, derive the delta, and write review-only XML."""
    sync_primary_english.synchronize(english_source, source, write=False)
    vanilla, fingerprints = read_snapshot(snapshot)
    english_locale = next(
        locale for locale in vanilla
        if locale.casefold() == SOURCE_LOCALE.casefold()
    )
    report = primary_audit.parse_source(source, SOURCE_LOCALE)
    localizations, references, summary = load_repository_localizations(
        source, art_root, root,
    )
    catalog = build_catalog(
        report, vanilla[english_locale], sorted(vanilla),
        snapshot.name, fingerprints[english_locale],
        localizations, references, summary,
    )
    files = preview_files(catalog, list(vanilla))
    write_preview(output, files, snapshot, root=root)
    return catalog["summary"]


def main() -> int:
    """Build review-only XML from the frozen vanilla reference."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vanilla-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW)
    args = parser.parse_args()
    try:
        summary = build_preview(args.vanilla_snapshot, args.output)
    except (CatalogError, OSError, ValueError) as error:
        parser.exit(1, f"Fallback preview failed: {error}\n")
    print(
        f"Review only: {summary['requires_translation']} English "
        f"rows; {summary['requires_source_review']} source conflicts withheld."
    )
    print(f"Preview: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
