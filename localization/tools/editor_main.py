"""Start the browser editor or run the downloaded GUI EXE as its updater."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback


def main() -> int:
    """Keep background failures readable when Windows launches without a console."""
    if getattr(sys, "frozen", False) and sys.stderr is None:
        home = Path(sys.executable).resolve().parent
        log = home / "localization/workspace/editor-startup.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        # Embedded project tools print progress. A windowed EXE has no stdio;
        # send their output to a file rather than crashing on print().
        sys.stdout = log.open("a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout

        def report(error_type, error, trace):
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as handle:
                    traceback.print_exception(error_type, error, trace, file=handle)
            except OSError:
                pass

        sys.excepthook = report
    if len(sys.argv) > 1 and sys.argv[1] == "--install-update":
        from lekmod_localization.editor_update import installer_main
        return installer_main(sys.argv[2:])
    from editor_server import main as editor_main
    return editor_main()


if __name__ == "__main__":
    raise SystemExit(main())
