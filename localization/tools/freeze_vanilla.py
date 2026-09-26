"""Create or verify a local, untracked reference of official Civ V text."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

from lekmod_localization.common import CatalogError, REPO_ROOT
from lekmod_localization.vanilla_snapshot import (
    read_snapshot,
    verify_snapshot,
    write_snapshot,
)
from lekmod_localization.vanilla_reference import (
    DEFAULT_REFERENCE, verify_snapshot_reference, write_reference,
)


DEFAULT_SNAPSHOT = REPO_ROOT / "localization" / "workspace" / "vanilla-snapshot.json.gz"


def main() -> int:
    """Freeze a clean vanilla database or verify an existing snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vanilla-db", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--check", action="store_true", help="Compare a frozen copy with the database")
    parser.add_argument("--refresh-reference", action="store_true",
                        help="Explicitly update the tracked fingerprint index from a reviewed snapshot")
    args = parser.parse_args()
    try:
        if args.refresh_reference:
            if args.vanilla_db or args.check:
                parser.error("--refresh-reference uses an existing snapshot, not --vanilla-db/--check")
            write_reference(args.output, DEFAULT_REFERENCE)
            print(f"Pinned reference refreshed: {DEFAULT_REFERENCE}")
        elif args.check:
            if not args.vanilla_db:
                parser.error("--check requires --vanilla-db")
            verify_snapshot(args.vanilla_db, args.output)
        else:
            if not args.vanilla_db:
                parser.error("provide --vanilla-db or --refresh-reference")
            write_snapshot(args.vanilla_db, args.output)
        if not args.refresh_reference:
            verify_snapshot_reference(args.output)
        locales, _ = read_snapshot(args.output)
    except (CatalogError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Vanilla snapshot failed: {error}\n")
    print(f"Vanilla snapshot: {args.output} ({len(locales)} locales)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
