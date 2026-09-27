"""Encrypt a verified vanilla snapshot or download and decrypt its HTTPS copy."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.request

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ModuleNotFoundError as error:
    if error.name != "cryptography":
        raise
    raise SystemExit(
        "cryptography is unavailable for this Python. On Windows ARM64, use "
        "Lekmod Localization Editor v0.5 or newer to encrypt the snapshot in "
        "Settings → Vanilla reference; no pip installation is needed."
    ) from error

from lekmod_localization.common import CatalogError, REPO_ROOT
from lekmod_localization.vanilla_reference import verify_snapshot_reference


MAGIC = b"LEKMOD-SNAPSHOT-AESGCM-1\0"
MAX_ARCHIVE = 20 * 1024 * 1024
DEFAULT_SNAPSHOT = REPO_ROOT / "localization/workspace/vanilla-snapshot.json.gz"


def _key(password: str, salt: bytes) -> bytes:
    """Derive a different AES key for each archive using memory-hard scrypt."""
    if len(password) < 16:
        raise ValueError("Use a strong password of at least 16 characters; '111' is unsafe")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**15, r=8,
                          p=1, dklen=32, maxmem=128 * 1024 * 1024)


def encrypt_snapshot(source: Path, password: str, reference: Path, output: Path) -> Path:
    """Verify against the pinned baseline before encrypting the exact gzip bytes."""
    verify_snapshot_reference(source, reference)
    data = source.read_bytes()
    if len(data) > MAX_ARCHIVE:
        raise ValueError("vanilla snapshot exceeds 20 MB")
    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"encrypted archive already exists: {output}")
    with output.open("xb") as handle:
        handle.write(MAGIC + salt + nonce + cipher)
    return output


def decrypt_snapshot(archive: bytes, password: str, reference: Path) -> bytes:
    """Authenticate the password and compare every locale with the team baseline."""
    if not archive.startswith(MAGIC) or len(archive) > MAX_ARCHIVE + 128:
        raise ValueError("not a supported encrypted Lekmod snapshot")
    start = len(MAGIC)
    salt, nonce = archive[start:start + 16], archive[start + 16:start + 28]
    try:
        data = AESGCM(_key(password, salt)).decrypt(nonce, archive[start + 28:], MAGIC)
    except InvalidTag as error:
        raise ValueError("wrong password or damaged encrypted snapshot") from error
    with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        verify_snapshot_reference(temporary, reference)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def download_encrypted(url: str) -> bytes:
    """Retrieve a direct HTTPS file link, bounding redirects and response size."""
    if not url.startswith("https://") or len(url) > 4096 or "@" in url.split("/", 3)[2]:
        raise ValueError("enter an HTTPS direct-download link")
    request = urllib.request.Request(url, headers={"User-Agent": "Lekmod-Localization-Editor"})
    with urllib.request.urlopen(request, timeout=45) as response:
        if not response.url.startswith("https://"):
            raise ValueError("snapshot download was redirected away from HTTPS")
        data = response.read(MAX_ARCHIVE + 129)
    if len(data) > MAX_ARCHIVE + 128:
        raise ValueError("encrypted snapshot exceeds 20 MB")
    return data


def install_snapshot(data: bytes, destination: Path) -> None:
    """Atomically save a verified snapshot without placing plaintext in Git."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".snapshot.",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    """Prompt for a password so it does not enter shell history or arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    encrypt = commands.add_parser("encrypt")
    encrypt.add_argument("--input", type=Path, default=DEFAULT_SNAPSHOT)
    encrypt.add_argument("--output", type=Path,
                         default=REPO_ROOT / "localization/workspace/vanilla-snapshot.enc")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    reference = REPO_ROOT / "localization/reference/vanilla-fingerprints.json.gz"
    try:
        password = getpass.getpass("Snapshot password (at least 16 characters): ")
        if args.action == "encrypt":
            repeated = getpass.getpass("Repeat password: ")
            if password != repeated:
                raise ValueError("passwords do not match")
            print(f"Encrypted: {encrypt_snapshot(args.input, password, reference, args.output)}")
        else:
            data = decrypt_snapshot(download_encrypted(args.url), password, reference)
            install_snapshot(data, args.output)
            print(f"Verified snapshot: {args.output}")
    except (CatalogError, OSError, ValueError) as error:
        parser.exit(1, f"Snapshot transfer failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
