"""Scan the files git would commit (tracked + untracked, minus .gitignore) for strings shaped like provider credentials.

    python scripts/scan_secrets.py            # from developer-rag/ or the repo root; exit 1 if anything looks like a key

Stdlib only, so CI can run it before installing anything. It prints file:line and the KIND of match with the first four characters,
never the whole value. Limits: it knows the shapes listed in PATTERNS, so an unfamiliar key format, or a secret that is only a
password-like word, is not found; it is a tripwire for the common accident (a pasted key), not a guarantee.
A line may carry `secret-scan: ignore` to be skipped (use it only for a deliberate, obviously fake fixture).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("groq-style key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    ("anthropic-style key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("hugging face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
SKIP_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bundle", ".sqlite3", ".db", ".bin", ".zip", ".docx", ".pyc"}
MAX_BYTES = 2_000_000
IGNORE_MARK = "secret-scan: ignore"


def candidate_files(root: Path) -> List[Path]:
    """What `git add -A` would add: tracked and untracked files that are not ignored."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True, check=True).stdout
    return [root / p for p in out.decode("utf-8", "replace").split("\0") if p]


def scan_text(text: str) -> List[Tuple[int, str, str]]:
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        if IGNORE_MARK in line:
            continue
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if m:
                hits.append((n, kind, m.group(0)[:4] + "..."))
    return hits


def scan(files: Iterable[Path], root: Path) -> List[Tuple[str, int, str, str]]:
    found = []
    for f in files:
        try:
            if f.suffix.lower() in SKIP_SUFFIXES or not f.is_file() or f.stat().st_size > MAX_BYTES:
                continue
            text = f.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for n, kind, preview in scan_text(text):
            found.append((f.relative_to(root).as_posix(), n, kind, preview))
    return found


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    root = Path(top.stdout.strip())
    files = candidate_files(root)
    found = scan(files, root)
    for path, n, kind, preview in found:
        print(f"{path}:{n}: looks like a {kind} ({preview})")
    print(f"scanned {len(files)} file(s); {len(found)} suspicious line(s)")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
