"""One command for configured localization preparation and checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from lekmod_localization.common import CatalogError, REPO_ROOT, WORKSPACE
from lekmod_localization.vanilla_reference import DEFAULT_REFERENCE, verify_snapshot_reference


LOCALIZATION = REPO_ROOT / "localization"
TOOLS = LOCALIZATION / "tools"
CONFIG = LOCALIZATION / "config.json"
SNAPSHOT = WORKSPACE / "vanilla-snapshot.json.gz"
LEGACY_WORKSPACE = REPO_ROOT / "build" / "localization"
CHECKS = ("art", "primary", "english_sync", "inventory", "unit_tests", "shipped")
BUILD = ("english", "catalog", "shipped")


def read_config(path: Path = CONFIG) -> dict[str, dict[str, bool]]:
    """Validate every switch so a typo cannot silently disable a check."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CatalogError(f"cannot read localization config: {error}") from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "checks", "build"} or raw["schema_version"] != 1:
        raise CatalogError("unsupported localization config")
    result = {}
    for group, names in (("checks", CHECKS), ("build", BUILD)):
        values = raw.get(group)
        if not isinstance(values, dict) or set(values) != set(names):
            raise CatalogError(f"config {group} must contain exactly: {', '.join(names)}")
        if any(value not in ("On", "Off") for value in values.values()):
            raise CatalogError(f"config {group} accepts only On or Off")
        result[group] = {name: values[name] == "On" for name in names}
    return result


def run(*args: str) -> None:
    """Run one tool from the repository root and stop on failure."""
    subprocess.run([sys.executable, "-B", *args], cwd=REPO_ROOT, check=True)


def migrate_workspace(destination: Path = WORKSPACE, legacy: Path = LEGACY_WORKSPACE) -> None:
    """Move old private CSV drafts and snapshot once, without overwriting either."""
    if legacy.is_dir() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(destination))
        print(f"Existing localization workspace moved to {destination}", flush=True)
    elif legacy.is_dir() and destination.is_dir() and (
        not (destination / "vanilla-snapshot.json.gz").is_file()
    ):
        raise CatalogError(
            "both old and new workspaces exist; move the vanilla snapshot and drafts "
            "manually before continuing"
        )


def prepare(config: dict[str, dict[str, bool]], snapshot: Path) -> None:
    """Update generated XML locally before committing its checked-in copy."""
    verify_snapshot_reference(snapshot)
    if config["build"]["english"]:
        run(str(TOOLS / "sync_primary_english.py"), "--write")
    if config["build"]["catalog"]:
        run(str(TOOLS / "build_localization_catalog.py"), "--vanilla-snapshot", str(snapshot))
    if config["build"]["shipped"]:
        run(str(TOOLS / "build_shipped_localization.py"), "--vanilla-snapshot", str(snapshot), "--write")


def check(config: dict[str, dict[str, bool]], snapshot: Path, *, ci: bool) -> None:
    """Run enabled checks with the pinned reference in CI."""
    commands = {
        "art": (TOOLS / "audit_localization.py", "--strict"),
        "primary": (TOOLS / "audit_primary_localization.py", "--strict"),
        "english_sync": (TOOLS / "sync_primary_english.py",),
        "inventory": (TOOLS / "inventory_localization.py", "--strict"),
    }
    for name in CHECKS:
        if not config["checks"][name]:
            print(f"Skipped {name}: Off", flush=True)
            continue
        if name == "unit_tests":
            run("-W", "error::ResourceWarning", "-m", "unittest", "discover",
                "-s", str(TOOLS / "tests"), "-q")
        elif name == "shipped":
            if ci:
                run(str(TOOLS / "build_shipped_localization.py"),
                    "--vanilla-reference", str(DEFAULT_REFERENCE), "--check")
            elif snapshot.is_file():
                verify_snapshot_reference(snapshot)
                run(str(TOOLS / "build_shipped_localization.py"),
                    "--vanilla-snapshot", str(snapshot), "--check")
            else:
                raise CatalogError(f"shipped check needs {snapshot}")
        else:
            script, *options = commands[name]
            run(str(script), *options)


def main() -> int:
    """Select the local preparation or read-only validation path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("--ci", action="store_true", help="Do not require the private snapshot in CI")
    parser.add_argument("--vanilla-snapshot", type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    try:
        if not args.ci:
            migrate_workspace()
        config = read_config()
        if args.action == "prepare":
            if args.ci:
                parser.error("--ci is for checks only")
            prepare(config, args.vanilla_snapshot)
        else:
            check(config, args.vanilla_snapshot, ci=args.ci)
    except (CatalogError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Localization {args.action} failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
