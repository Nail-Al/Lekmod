"""Keep a text-free English row date index for Git and portable source ZIPs."""

from __future__ import annotations

from datetime import datetime, timezone
from bisect import bisect_right
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def dates_from_git(source: Path, operations: list[dict], root: Path) -> dict[str, str]:
    """Use each text operation's newest blamed line, including local edits."""
    output = subprocess.run(
        ["git", "blame", "--line-porcelain", "--", str(source.relative_to(root))],
        cwd=root, check=True, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=90,
    ).stdout
    times = []
    current = None
    for line in output.splitlines():
        if line.startswith("author-time "):
            current = int(line.split(" ", 1)[1])
        elif line.startswith("\t"):
            times.append(current)
    document = source.read_text(encoding="utf-8")
    starts = [0] + [match.end() for match in re.finditer("\n", document)]
    dates: dict[str, str] = {}
    for row in operations:
        start = bisect_right(starts, row["start"]) - 1
        finish = bisect_right(starts, row["text_end"]) - 1
        stamps = [stamp for stamp in times[start:finish + 1] if stamp is not None]
        if stamps:
            date = datetime.fromtimestamp(max(stamps), timezone.utc).isoformat()
            dates[row["key"]] = max(dates.get(row["key"], ""), date)
    return dates


def read_dates(source: Path, operations: list[dict], root: Path) -> dict[str, str]:
    """Use live Git history where possible; otherwise load the pinned date index."""
    if (root / ".git").exists() and shutil.which("git"):
        return dates_from_git(source, operations, root)
    path = root / "localization/reference/english-edit-dates.json.gz"
    if path.is_file():
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            saved = json.load(handle)
        if saved.get("source_sha256") == hashlib.sha256(source.read_bytes()).hexdigest():
            return saved.get("dates", {})
    return {}


def write_dates(source: Path, operations: list[dict], root: Path) -> Path:
    """Ship dates without shipping Git history in the downloadable project."""
    dates = dates_from_git(source, operations, root)
    content = {"schema_version": 1,
               "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "dates": dates}
    path = root / "localization/reference/english-edit-dates.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(content, sort_keys=True,
                                             separators=(",", ":")).encode(), mtime=0))
    return path
