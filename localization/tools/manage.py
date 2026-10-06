"""One command for configured localization preparation and checks."""

from __future__ import annotations

import argparse
import importlib
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
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "checks", "build", "_help"} or raw["schema_version"] != 1:
        raise CatalogError("unsupported localization config")
    help_text = raw["_help"]
    if not isinstance(help_text, dict) or set(help_text) != {"checks", "build"}:
        raise CatalogError("localization config needs English help for checks and build")
    result = {}
    for group, names in (("checks", CHECKS), ("build", BUILD)):
        values = raw.get(group)
        if not isinstance(values, dict) or set(values) != set(names):
            raise CatalogError(f"config {group} must contain exactly: {', '.join(names)}")
        if any(value not in ("On", "Off") for value in values.values()):
            raise CatalogError(f"config {group} accepts only On or Off")
        descriptions = help_text[group]
        if not isinstance(descriptions, dict) or set(descriptions) != set(names) or any(
            not isinstance(value, str) or not value.strip() for value in descriptions.values()
        ):
            raise CatalogError(f"config _help.{group} must describe each switch")
        result[group] = {name: values[name] == "On" for name in names}
    return result


def run(*args: str) -> None:
    """Run one tool from the repository root and stop on failure."""
    if not getattr(sys, "frozen", False):
        subprocess.run([sys.executable, "-B", *args], cwd=REPO_ROOT, check=True)
        return
    # sys.executable is the editor itself in a frozen build. Dispatch inside
    # the embedded interpreter so it cannot recursively start another editor.
    script = Path(args[0]).stem
    if script not in {"sync_primary_english", "build_localization_catalog",
                      "build_shipped_localization", "build_editor_dates"}:
        raise CatalogError(f"unsupported packaged tool: {script}")
    previous = sys.argv
    sys.argv = [script, *args[1:]]
    try:
        result = importlib.import_module(script).main()
        if result:
            raise CatalogError(f"{script} failed with exit code {result}")
    except SystemExit as error:
        raise CatalogError(f"{script} failed: {error}") from error
    finally:
        sys.argv = previous


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


def ensure_workspace_private(root: Path = REPO_ROOT) -> None:
    """Reject accidental force-adds of the local vanilla text or CSV drafts."""
    if not (root / ".git").exists():
        return  # A portable contributor package has no Git index to inspect.
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "localization/workspace"],
        cwd=root, check=True, capture_output=True,
    )
    tracked = [name.decode("utf-8", "replace") for name in result.stdout.split(b"\0") if name]
    if tracked:
        raise CatalogError(
            "localization/workspace contains tracked private files: "
            + ", ".join(tracked[:5])
        )


def prepare(config: dict[str, dict[str, bool]], snapshot: Path | None) -> None:
    """Update generated XML locally before committing its checked-in copy."""
    if snapshot is not None:
        verify_snapshot_reference(snapshot)
    if config["build"]["english"]:
        run(str(TOOLS / "sync_primary_english.py"), "--write")
        if (REPO_ROOT / ".git").exists() and shutil.which("git"):
            run(str(TOOLS / "build_editor_dates.py"))
    if config["build"]["catalog"]:
        run(str(TOOLS / "build_localization_catalog.py"),
            "--vanilla-snapshot" if snapshot is not None else "--vanilla-reference",
            str(snapshot or DEFAULT_REFERENCE))
    if config["build"]["shipped"]:
        run(str(TOOLS / "build_shipped_localization.py"),
            "--vanilla-snapshot" if snapshot is not None else "--vanilla-reference",
            str(snapshot or DEFAULT_REFERENCE), "--write")


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
                run(str(TOOLS / "build_shipped_localization.py"),
                    "--vanilla-reference", str(DEFAULT_REFERENCE), "--check")
        else:
            script, *options = commands[name]
            run(str(script), *options)


def main() -> int:
    """Select the local preparation or read-only validation path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "diagnose-game"))
    parser.add_argument("--ci", action="store_true", help="Do not require the private snapshot in CI")
    parser.add_argument("--vanilla-snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--game-folder", type=Path, help="Civilization V installation for read-only diagnostics")
    parser.add_argument("--profile-folder", type=Path, help="Override the Civ V Documents profile for diagnostics")
    args = parser.parse_args()
    try:
        if args.action == 'diagnose-game':
            from lekmod_localization.connections import detect_game, game_diagnostics
            game = args.game_folder or (Path(path) if (path := detect_game()) else None)
            if game is None:
                raise CatalogError('Game not found; specify --game-folder')
            report = game_diagnostics(REPO_ROOT, game, profile=args.profile_folder)
            WORKSPACE.mkdir(parents=True, exist_ok=True)
            output = WORKSPACE / 'game-diagnostics.json'
            output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(f'Read-only game diagnostics: {output}')
            return 0
        ensure_workspace_private()
        if not args.ci:
            migrate_workspace()
        config = read_config()
        if args.action == "prepare":
            if args.ci:
                parser.error("--ci is for checks only")
            if args.vanilla_snapshot != SNAPSHOT and not args.vanilla_snapshot.is_file():
                raise CatalogError(f"requested vanilla snapshot is missing: {args.vanilla_snapshot}")
            prepare(config, args.vanilla_snapshot if args.vanilla_snapshot.is_file() else None)
        else:
            check(config, args.vanilla_snapshot, ci=args.ci)
    except (CatalogError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Localization {args.action} failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
