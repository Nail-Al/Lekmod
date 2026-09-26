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


DEFAULT_SNAPSHOT = REPO_ROOT / "build" / "localization" / "vanilla-snapshot.json.gz"


def main() -> int:
    """Freeze a clean vanilla database or verify an existing snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vanilla-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--check", action="store_true", help="Compare a frozen copy with the database")
    args = parser.parse_args()
    try:
        if args.check:
            verify_snapshot(args.vanilla_db, args.output)
        else:
            write_snapshot(args.vanilla_db, args.output)
        locales, _ = read_snapshot(args.output)
    except (CatalogError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Vanilla snapshot failed: {error}\n")
    print(f"Vanilla snapshot: {args.output} ({len(locales)} locales)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
