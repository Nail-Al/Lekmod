"""Review and merge a translator ZIP by locale and TXT_KEY without replacing a CSV."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
import zipfile

from lekmod_localization.common import CatalogError, KEY_RE, PLACEHOLDER_RE, tokens_match
from lekmod_localization.vanilla_reference import compatible_reference_digest
from sync_primary_english import marked_block


FIELDS = ("key", "source_fingerprint", "text", "gender", "plurality",
          "translator_note", "updated_at")
MEANING = FIELDS[:-1]  # Changing only the save timestamp is not a conflict.
MAX_ARCHIVE = 48 * 1024 * 1024
MAX_CSV = 8 * 1024 * 1024
LOCALE = re.compile(r"[A-Z]{2,8}(?:_[A-Z0-9]{2,8}){1,2}")


def csv_records(data: bytes, name: str) -> dict[str, dict[str, str]]:
    """Accept either tracked six-column CSVs or the editor's seven-column CSVs."""
    if len(data) > MAX_CSV:
        raise CatalogError(f"translation CSV is too large: {name}")
    try:
        with io.StringIO(data.decode("utf-8-sig"), newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames not in (list(FIELDS), list(FIELDS[:-1])):
                raise CatalogError(f"unexpected CSV columns: {name}")
            records = {}
            for row in reader:
                key = row.get("key", "")
                if (None in row or any(value is None for value in row.values())
                        or not isinstance(key, str) or not KEY_RE.fullmatch(key)
                        or key in records
                        or not re.fullmatch(r"[0-9a-f]{64}", row["source_fingerprint"])
                        or not row["text"]):
                    raise CatalogError(f"invalid, empty or repeated translation: {name} {key}")
                row.setdefault("updated_at", "")
                records[key] = row
                if len(records) > 20000:
                    raise CatalogError(f"too many translated keys: {name}")
            return records
    except (UnicodeError, csv.Error) as error:
        raise CatalogError(f"invalid translation CSV: {name}: {error}") from error


def current_source_rows(project: Path, locale: str) -> dict[str, dict]:
    """Refuse an English index built before either source XML changed."""
    workspace = project / "localization/workspace/editor"
    try:
        manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
        source = (project / "LEKMOD/Override/CIV5Units_Mongol.xml").read_text(encoding="utf-8")
        primary = (project / "localization/en_US/primary.xml").read_bytes()
        english = marked_block(source)[2]
    except (OSError, ValueError, UnicodeError) as error:
        raise CatalogError("run localization/tools/manage.py prepare before merging") from error
    if (manifest.get("source_english_sha256") != hashlib.sha256(english.encode("utf-8")).hexdigest()
            or manifest.get("primary_sha256") != hashlib.sha256(primary).hexdigest()):
        raise CatalogError("English changed; run manage.py prepare again")
    files = manifest.get("locales", {}).get(locale, {}).get("files", {})
    if not isinstance(files, dict) or not files:
        raise CatalogError(f"the editor catalog has no {locale} rows; run prepare again")
    result = {}
    for filename in files:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.csv", filename):
            raise CatalogError("unsafe generated catalog filename")
        with (workspace / locale / filename).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                key = row["key"]
                if key in result:
                    raise CatalogError(f"repeated generated English key: {key}")
                result[key] = row
    return result


def handoff_rows(archive: Path | bytes, reference: bytes) -> tuple[dict, dict]:
    """Read a bounded ZIP without extracting paths into the checkout."""
    if isinstance(archive, Path):
        if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
            raise CatalogError("handoff ZIP is missing or too large")
        source = archive
    else:
        if not isinstance(archive, bytes) or len(archive) > MAX_ARCHIVE:
            raise CatalogError("handoff ZIP is too large")
        source = io.BytesIO(archive)
    try:
        with zipfile.ZipFile(source) as package:
            names = package.namelist()
            if (len(names) < 3 or len(names) > 11 or len(set(names)) != len(names)
                    or {"manifest.json", "README.txt"} - set(names)):
                raise CatalogError("handoff ZIP needs a manifest, README and 1–9 language CSVs")
            members = sorted(set(names) - {"manifest.json", "README.txt"})
            if any(not re.fullmatch(r"translations/[A-Z0-9_]+\.csv", name) for name in members):
                raise CatalogError("unexpected handoff ZIP member")
            if any(info.file_size > MAX_CSV for info in package.infolist()):
                raise CatalogError("handoff ZIP member is too large")
            metadata = json.loads(package.read("manifest.json"))
            if (not isinstance(metadata, dict) or
                    not compatible_reference_digest(metadata.get("vanilla_reference_sha256"), reference)):
                raise CatalogError("handoff team vanilla reference differs from this project")
            locales = [name.removeprefix("translations/").removesuffix(".csv") for name in members]
            if any(not LOCALE.fullmatch(locale) for locale in locales):
                raise CatalogError("invalid handoff language")
            declared = metadata.get("locales")
            if declared is None and len(locales) == 1 and metadata.get("locale") == locales[0]:
                digests = {locales[0]: metadata.get("translation_csv_sha256")}
            elif isinstance(declared, dict) and set(declared) == set(locales):
                digests = declared
            else:
                raise CatalogError("handoff languages differ from its manifest")
            rows = {}
            for locale, name in zip(locales, members):
                content = package.read(name)
                digest = digests[locale]
                if digest is not None and digest != hashlib.sha256(content).hexdigest():
                    raise CatalogError(f"{locale} CSV differs from its manifest")
                rows[locale] = csv_records(content, name)
            return metadata, rows
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as error:
        if isinstance(error, CatalogError):
            raise
        raise CatalogError(f"invalid handoff ZIP: {error}") from error


def encoded_records(rows: dict[str, dict[str, str]]) -> bytes:
    """Keep the editor's BOM and seven-column CSV format after a safe union."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows[key] for key in sorted(rows))
    return stream.getvalue().encode("utf-8-sig")


def merge_handoff(archive: Path | bytes, project: Path, *, apply: bool = False,
                  choices: dict[str, str] | None = None,
                  expected: dict[str, str] | None = None,
                  sources: dict[str, dict] | None = None) -> dict:
    """Review every key; apply selected current rows atomically across all languages."""
    project = project.resolve()
    reference = (project / "localization/reference/vanilla-fingerprints.json.gz").read_bytes()
    metadata, incoming = handoff_rows(archive, reference)
    choices = choices or {}
    existing, original, target, source_rows = {}, {}, {}, {}
    for locale in incoming:
        target[locale] = project / "localization/translations" / f"{locale}.csv"
        if not target[locale].is_file():
            raise CatalogError(f"this project has no {locale} translation CSV")
        original[locale] = target[locale].read_bytes()
        existing[locale] = csv_records(original[locale], str(target[locale]))
        source_rows[locale] = sources if sources is not None else current_source_rows(project, locale)
    hashes = {locale: hashlib.sha256(content).hexdigest()
              for locale, content in original.items()}
    if expected is not None and hashes != expected:
        raise CatalogError("team translations changed since preview; import the ZIP again")
    items, pending, updates = [], [], {locale: {} for locale in incoming}
    identical_count = 0
    for locale, rows in sorted(incoming.items()):
        for key, row in sorted(rows.items()):
            item_id = f"{locale}:{key}"
            current = source_rows[locale].get(key)
            team = existing[locale].get(key)
            if team is not None and all(team[field] == row[field] for field in MEANING):
                status = "identical"
            elif (current is None or row["source_fingerprint"] != current["source_fingerprint"]
                  or PLACEHOLDER_RE.search(row["text"]) or
                  not tokens_match(row["text"], json.loads(current["required_format_tokens"]))):
                status = "stale"
            else:
                status = "conflict" if team is not None else "new"
            decision = choices.get(item_id, "incoming" if status == "new" else
                                   "keep" if status == "identical" else "review")
            if decision not in ("incoming", "keep", "review") or (
                    status in ("stale", "identical") and decision == "incoming"):
                raise CatalogError(f"invalid merge choice for {item_id}")
            if status in ("conflict", "stale") and decision == "review":
                pending.append(item_id)
            if decision == "incoming" and status in ("new", "conflict"):
                updates[locale][key] = row
            if status == "identical":
                identical_count += 1
                continue
            items.append({
                "id": item_id, "locale": locale, "key": key, "status": status,
                "choice": decision, "english": current.get("lekmod_en_US", "") if current else "",
                "team": team["text"] if team else "", "incoming": row["text"],
                "team_gender": team.get("gender", "") if team else "",
                "incoming_gender": row["gender"],
                "team_plurality": team.get("plurality", "") if team else "",
                "incoming_plurality": row["plurality"],
                "team_note": team.get("translator_note", "") if team else "",
                "incoming_note": row["translator_note"],
                "team_updated_at": team.get("updated_at", "") if team else "",
                "incoming_updated_at": row["updated_at"],
            })
    if set(choices) - {item["id"] for item in items}:
        raise CatalogError("merge choices contain an unknown language or key")
    head = None
    if (project / ".git").exists():
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True,
                              capture_output=True, text=True).stdout.strip()
    report = {"locales": sorted(incoming), "items": items, "pending": pending,
              "identical_count": identical_count,
              "target_sha256": hashes, "source_commit": metadata.get("repository_commit"),
              "current_commit": head, "applied": False, "backups": {}}
    if apply:
        if pending:
            raise CatalogError("resolve every stale/conflicting key before applying: " +
                               ", ".join(pending[:10]))
        affected = [locale for locale, rows in updates.items() if rows]
        if affected:
            temporary = {}
            try:
                for locale in affected:
                    payload = encoded_records({**existing[locale], **updates[locale]})
                    with tempfile.NamedTemporaryFile(dir=target[locale].parent,
                                                     prefix=".handoff-", delete=False) as handle:
                        temporary[locale] = Path(handle.name)
                        handle.write(payload)
                if any(target[locale].read_bytes() != original[locale] for locale in incoming):
                    raise CatalogError("team CSV changed during review; preview the handoff again")
                for locale in affected:
                    backup = project / "localization/workspace/handoff-backups" / (
                        f"{locale}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-"
                        f"{uuid.uuid4().hex[:6]}.csv")
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(original[locale])
                    report["backups"][locale] = str(backup)
                if any(target[locale].read_bytes() != original[locale] for locale in incoming):
                    raise CatalogError("team CSV changed before installation; preview again")
                written = []
                try:
                    for locale in affected:
                        temporary[locale].replace(target[locale])
                        written.append(locale)
                except OSError:
                    for locale in written:
                        target[locale].write_bytes(original[locale])
                    raise
                report["applied"] = True
            finally:
                for path in temporary.values():
                    path.unlink(missing_ok=True)
    return report


def main() -> int:
    """Preview by default; --apply requires explicit resolution of every conflict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path, help="ZIP downloaded with Exchange")
    parser.add_argument("--project", type=Path, default=Path.cwd(),
                        help="maintainer's checkout (default: current folder)")
    parser.add_argument("--apply", action="store_true", help="write chosen rows with private backups")
    parser.add_argument("--use-incoming", action="append", default=[], metavar="LOCALE:TXT_KEY",
                        help="accept this conflicting translation")
    parser.add_argument("--keep", action="append", default=[], metavar="LOCALE:TXT_KEY",
                        help="keep the current translation or skip this stale row")
    args = parser.parse_args()
    if set(args.use_incoming) & set(args.keep):
        parser.error("the same row cannot be accepted and kept")
    choices = {**{key: "incoming" for key in args.use_incoming},
               **{key: "keep" for key in args.keep}}
    try:
        report = merge_handoff(args.zip, args.project, apply=args.apply, choices=choices)
    except (CatalogError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Handoff merge failed: {error}\n")
    print("Languages: " + ", ".join(report["locales"]))
    for item in report["items"]:
        if item["status"] != "identical":
            print(f"{item['id']} [{item['status']}] {item['choice']}: "
                  f"team {item['team'][:160]!r} -> handoff {item['incoming'][:160]!r}")
            if any(item["team_" + field] != item["incoming_" + field]
                   for field in ("gender", "plurality", "note")):
                print("  grammar/note: team " +
                      repr(tuple(item["team_" + field] for field in ("gender", "plurality", "note"))) +
                      " -> handoff " +
                      repr(tuple(item["incoming_" + field] for field in ("gender", "plurality", "note"))))
    if report["source_commit"] != report["current_commit"]:
        print("Source commit differs or was unavailable; English rows were checked individually.")
    if report["pending"]:
        print("Review these rows with --use-incoming or --keep: " + ", ".join(report["pending"]))
        return 2
    if report["applied"]:
        print("Merged selected rows. Backups: " + ", ".join(report["backups"].values()))
        print("Run manage.py prepare and check, then review the CSVs and generated XML.")
    elif not args.apply:
        print("Preview only. Re-run with --apply after reviewing these rows.")
    else:
        print("No rows changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
