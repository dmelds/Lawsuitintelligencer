#!/usr/bin/env python3
"""
Load the letter's bot check (/letter-guard.js) on every page with a signup form.

Every <form> that posts to /api/subscribe gets one line right after its closing </form>:

    <script src="/letter-guard.js" defer></script>

indented like the form itself. The script reads the form, so it has to sit on the same page; one
tag per page is enough even if a page ever carries two forms, because the script finds every
signup form on the page.

The Congress and State tracker generators (scripts/generate_congress_board.py and
scripts/generate_state_board.py) write the same line themselves, so a tracker rebuild keeps it.
This script covers the hand-kept pages (homepage, Mass Tort Map, the dockets, the State AI
Enforcement Tracker, /newsletter) and any page added later.

The line is page furniture, so it never moves a page's dateModified or visible stamp; the
workflow (.github/workflows/letter-guard.yml) commits with [skip lastmod] [skip actions]. A page
is written only when it lacks the line, so a rerun on a swept tree changes nothing.

    python3 scripts/add_letter_guard.py            # write the pages that lack the line
    python3 scripts/add_letter_guard.py --dry-run  # list them, write nothing
    python3 scripts/add_letter_guard.py --check    # exit 1 when a page lacks the line
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = '<script src="/letter-guard.js" defer></script>'
FORM = re.compile(r'(?m)^([ \t]*)<form\b[^>]*\baction="/api/subscribe"[^>]*>')


def place(text: str) -> tuple[str, str]:
    """(new_text, what happened): 'has it', 'added', or 'no form'."""
    m = FORM.search(text)
    if not m:
        return text, "no form"
    if "/letter-guard.js" in text:
        return text, "has it"
    indent = m.group(1)
    close = text.find("</form>", m.end())
    if close == -1:
        return text, "no form"
    at = close + len("</form>")
    return text[:at] + "\n" + indent + TAG + text[at:], "added"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    os.chdir(ROOT)

    added, kept = [], []
    for path in sorted(glob.glob("*.html")):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        new, how = place(old)
        if how == "has it":
            kept.append(path)
        elif how == "added":
            added.append(path)
            if not (args.dry_run or args.check):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)

    verb = "lack the line" if args.check else ("would change" if args.dry_run else "changed")
    print(f"{len(kept)} page(s) already load the bot check")
    print(f"{len(added)} page(s) {verb}")
    for path in added:
        print(f"  {path}")
    if args.check and added:
        print("::error::a page with a signup form does not load /letter-guard.js; run scripts/add_letter_guard.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
