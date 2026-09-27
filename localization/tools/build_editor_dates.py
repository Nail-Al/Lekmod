"""Refresh the text-free English edit dates before sharing a source snapshot."""

from lekmod_localization.common import REPO_ROOT
from lekmod_localization.english_dates import write_dates
from editor_server import primary_operations


def main() -> int:
    """Write the date index for the current canonical English XML."""
    source = REPO_ROOT / "localization/en_US/primary.xml"
    rows = primary_operations(source.read_text(encoding="utf-8"))
    print(f"English row dates: {write_dates(source, rows, REPO_ROOT)} ({len(rows)} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
