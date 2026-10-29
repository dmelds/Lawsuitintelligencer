#!/usr/bin/env python3
"""Roll the masthead issue line on Lawsuit Intelligencer's evergreen pages.

check_issue_line.py splits the masthead two ways. An article's issue line is an
archive stamp and never moves. An evergreen page (about, contribute, the index
and the rest of its EVERGREEN set) states the current issue, so on the 1st of
every month it goes one issue behind and the checker warns. Until now that roll
was done by hand across nine pages. This script does it.

Two evergreen pages are left out on purpose. mass-tort-map and
state-ai-enforcement-tracker are living pages: their masthead states the issue
the reader is holding, and that changes when the page is rewritten with the
month's JPML report or the month's filings. Rolling them ahead of the rewrite
would claim a November issue on October content, so they roll with their
rewrite, by hand.

Articles are never touched: only pages in check_issue_line.EVERGREEN are read.

Usage
-----
    python3 scripts/roll_issue_line.py              # roll to this month (UTC)
    python3 scripts/roll_issue_line.py --dry-run    # print, write nothing
    python3 scripts/roll_issue_line.py --month 2026-11
    python3 scripts/roll_issue_line.py --path .     # site root (default .)
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_issue_line as cil  # noqa: E402

LIVING = {"mass-tort-map.html", "state-ai-enforcement-tracker.html"}
ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def roman(n):
    out = ""
    for value, sym in ROMAN:
        while n >= value:
            out += sym
            n -= value
    return out


def line_for(year, month, sep):
    """The masthead line for a calendar month. Twelve issues to a volume."""
    number = cil.issue_number(year, month)
    vol = roman((number - 1) // 12 + 1)
    return f"Vol. {vol} {sep} No. {number} {sep} {cil.NAMES[month]} {year}"


def main():
    ap = argparse.ArgumentParser(description="Roll evergreen masthead issue lines.")
    ap.add_argument("--month", help="target month YYYY-MM (default: this month, UTC)")
    ap.add_argument("--path", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.month:
        year, month = (int(x) for x in args.month.split("-")[:2])
    else:
        now = datetime.now(timezone.utc)
        year, month = now.year, now.month
    target = cil.issue_number(year, month)
    root = Path(args.path)

    rolled, current, problems = [], [], []
    for name in sorted(cil.EVERGREEN - LIVING):
        path = root / name
        if not path.exists():
            problems.append(f"{name}: in EVERGREEN but not on disk")
            continue
        html = path.read_text(encoding="utf-8")
        found = list(cil.ISSUE.finditer(html))
        if not found:
            problems.append(f"{name}: no masthead issue line")
            continue
        new_html, before = html, None
        for m in reversed(found):
            number = int(m.group(2))
            before = m.group(0)
            if number > target:
                problems.append(f"{name}: reads No. {number}, ahead of No. {target}; left alone")
                new_html = None
                break
            sep = "&middot;" if "&middot;" in m.group(0) else "·"
            new_html = new_html[:m.start()] + line_for(year, month, sep) + new_html[m.end():]
        if new_html is None:
            continue
        if new_html == html:
            current.append(name)
            continue
        rolled.append((name, before.replace("&middot;", "·")))
        if not args.dry_run:
            path.write_text(new_html, encoding="utf-8")

    label = line_for(year, month, "·")
    print(f"Issue roll to {label}{' (dry run)' if args.dry_run else ''}")
    for name, before in rolled:
        print(f"  rolled   {name}  (was {before})")
    for name in current:
        print(f"  current  {name}")
    for name in sorted(LIVING):
        print(f"  held     {name}  (living page: rolls with its monthly rewrite)")
    for line in problems:
        print(f"  PROBLEM  {line}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
