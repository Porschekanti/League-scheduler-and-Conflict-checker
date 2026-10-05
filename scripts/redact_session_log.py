"""
Redact secrets out of a Claude Code session log before it is committed.

Session transcripts are useful coursework evidence, but they record everything
that was typed — including, in at least one session here, a real account
password pasted into the chat. This repository is public, so a raw transcript
is a credential leak waiting to happen.

This strips the things that must never be published and leaves the rest of the
conversation intact, so the log is still worth reading.

    python scripts/redact_session_log.py IN.jsonl OUT.jsonl
    python scripts/redact_session_log.py --scan IN.jsonl      # report only

What it removes:
  * values following a password/secret/token label
  * PEM private key blocks
  * JWTs (three base64url segments)
  * AWS-style access keys and Google API keys
  * `sk-`/`ghp_`/`gho_` style provider tokens
  * email addresses, which are personal data rather than secrets, replaced with
    a stable placeholder so the conversation still reads sensibly

It deliberately does NOT try to be clever about context. A false positive costs
a few redacted characters in a log; a false negative publishes a credential.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REDACTION = "[REDACTED]"

# Ordered most-specific first; each is applied to the whole line.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PEM private key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("labelled secret", re.compile(
        r"((?:password|passwd|pwd|secret|api[_ -]?key|access[_ -]?token|auth[_ -]?token|"
        r"client[_ -]?secret|private[_ -]?key)\s*[:=]\s*)"
        r"([^\s\",;\\]{4,})", re.IGNORECASE)),
    ("provider token", re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,})")),
    ("AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("email address", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]

# Addresses that are fixtures, not real people — keep them so the log still
# demonstrates the seeded accounts the coursework actually uses.
EMAIL_ALLOWLIST = {
    "head@example.edu", "bball-rep@example.edu", "cricket-rep@example.edu",
    "nominee@example.edu", "new-rep@example.edu", "incoming@example.edu",
    "viewer@example.edu", "noreply@anthropic.com",
}


def _redact_email(match: re.Match[str]) -> str:
    address = match.group(0)
    if address.lower() in EMAIL_ALLOWLIST or address.endswith("example.com"):
        return address
    if address.endswith(".iam.gserviceaccount.com"):
        return "[SERVICE-ACCOUNT-REDACTED]"
    return "[EMAIL-REDACTED]"


def redact(text: str, literals: list[str] | None = None) -> tuple[str, dict[str, int]]:
    """Scrub `text`. `literals` are exact strings to remove everywhere.

    The literal pass matters more than it looks. Label-based matching only
    catches a secret where it appears as `password: hunter2`, and a transcript
    records far more than the original message — a later `grep -c "hunter2"`
    puts the value back in the log with no label in front of it. That is
    exactly how the first pass of this script leaked a password it had already
    redacted once. Literals are passed in at runtime and never stored here.
    """
    counts: dict[str, int] = {}

    for literal in literals or []:
        if not literal.strip():
            continue
        n = text.count(literal)
        if n:
            text = text.replace(literal, REDACTION)
            counts["literal"] = counts.get("literal", 0) + n

    def bump(name: str, n: int = 1) -> None:
        if n:
            counts[name] = counts.get(name, 0) + n

    for name, pattern in PATTERNS:
        if name == "email address":
            # subn would count allowlisted addresses that were deliberately left
            # alone, so count the placeholders that actually landed instead.
            before = text.count("[EMAIL-REDACTED]") + text.count("[SERVICE-ACCOUNT-REDACTED]")
            text = pattern.sub(_redact_email, text)
            after = text.count("[EMAIL-REDACTED]") + text.count("[SERVICE-ACCOUNT-REDACTED]")
            bump(name, after - before)
            continue
        if name == "labelled secret":
            text, n = pattern.subn(lambda m: m.group(1) + REDACTION, text)
        else:
            text, n = pattern.subn(REDACTION, text)
        bump(name, n)
    return text, counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("destination", type=Path, nargs="?")
    ap.add_argument("--scan", action="store_true",
                    help="report what would be redacted and write nothing")
    ap.add_argument("--literal", action="append", default=[], metavar="VALUE",
                    help="an exact string to scrub everywhere it appears; repeatable. "
                         "Pass real secrets this way so they are never stored in "
                         "this file or in shell history that gets committed.")
    ap.add_argument("--literals-file", type=Path,
                    help="file of exact strings to scrub, one per line (keep it "
                         "out of version control)")
    args = ap.parse_args()

    literals = list(args.literal)
    if args.literals_file and args.literals_file.exists():
        literals += [ln.strip() for ln in args.literals_file.read_text().splitlines()
                     if ln.strip() and not ln.startswith("#")]

    if not args.scan and args.destination is None:
        ap.error("a destination is required unless --scan is given")

    raw = args.source.read_text(errors="replace")
    cleaned, counts = redact(raw, literals)

    # Count the placeholders actually present, which is the honest measure.
    tally = {
        "redactions": cleaned.count(REDACTION),
        "emails": cleaned.count("[EMAIL-REDACTED]"),
        "service accounts": cleaned.count("[SERVICE-ACCOUNT-REDACTED]"),
    }
    print(f"{args.source.name}: " + ", ".join(f"{k}={v}" for k, v in tally.items()))

    if args.scan:
        return 0

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(cleaned)
    print(f"  -> {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
