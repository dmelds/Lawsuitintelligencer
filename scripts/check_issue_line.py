#!/usr/bin/env python3
"""Check the masthead issue line on every Lawsuit Intelligencer page.

Every page carries a masthead line of the form

    Vol. I &middot; No. N &middot; Month YYYY

and the publication treats it two different ways depending on the page. Getting
those two backwards is the failure this script exists to catch.

An ARTICLE states the issue it ran in. It is an archive stamp and it never
moves. chatgpt-log-preservation published on August 28, so it reads No. 4
August 2026 and will still read No. 4 August 2026 in 2028. Rolling a published
article forward to the current month is the false-freshness that
check_date_consistency.py exists to block on the Informer side, and it destroys
the archive at the same time.

An EVERGREEN page states the current issue. about, contribute, the index and
the rest carry no fixed date of their own, so they advance with the calendar.
These are the pages that go stale when a month turns over, and they are the
only ones a monthly reminder should ever name.

Rules
-----
ERROR  A page carries no masthead issue line, or one this script cannot parse.
       Every page in the repo has one, so a missing line means a new page was
       built from something other than the current template.
ERROR  The issue number and the month disagree with each other, e.g.
       "No. 4 &middot; September 2026". One of the two was edited alone.
ERROR  An article's issue line disagrees with its own JSON-LD datePublished.
       A page published in July belongs to the July issue. This is the rule
       that keeps the archive honest, and it blocks the merge under --strict.
WARN   An evergreen page's issue line is behind the month the check is running
       in. The page did not change; the calendar did. Warn only, and never on
       a PR: a merge should not fail for a reason the branch did not cause.
ERROR  An evergreen page's issue line is AHEAD of the current month. Nothing
       legitimate produces that, and a masthead claiming next month reads as
       broken rather than as early.

Issue numbering runs from Vol. I No. 1 = May 2026, one number per calendar
month, and is computed rather than tabulated so it keeps working into Vol. II
without anyone editing a list.

Usage
-----
    python3 check_issue_line.py            # report, exit 0
    python3 check_issue_line.py --strict   # exit 1 on any ERROR, calendar rule off
    python3 check_issue_line.py --path .   # scan root (default .)
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MONTHS = {m: i + 1 for i, m in enumerate([
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
])}
NAMES = {v: k for k, v in MONTHS.items()}

# Vol. I No. 1 is May 2026. Everything else is arithmetic from here.
EPOCH_YEAR, EPOCH_MONTH = 2026, 5

# Pages with no issue of their own, which therefore track the current month.
# interviews and david-meldofsky carry a JSON-LD datePublished because they are
# real pages with a first-published date, but neither is an article: the
# interviews hub re-lists every published interview and the author page is a
# standing bio. Both advance with the calendar like the rest of this set.
EVERGREEN = {
    "404.html",
    "about.html",
    "contribute.html",
    "david-meldofsky.html",
    "editorial-standards.html",
    "index.html",
    "interviews.html",
}

ISSUE = re.compile(
    r"Vol\.\s*([IVXL]+)\s*(?:&middot;|·)\s*No\.\s*(\d+)\s*"
    r"(?:&middot;|·)\s*([A-Z][a-z]+)\s+(20\d{2})"
)
LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def issue_number(year, month):
    """Issue number for a calendar month. May 2026 is 1."""
    return (year - EPOCH_YEAR) * 12 + (month - EPOCH_MONTH) + 1


def issue_month(number):
    """Inverse of issue_number: the (year, month) an issue number lands on."""
    offset = EPOCH_MONTH - 1 + (number - 1)
    return EPOCH_YEAR + offset // 12, offset % 12 + 1


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def published(html):
    """First JSON-LD datePublished on the page, as (year, month), or None."""
    for block in LD.findall(html):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in walk(data):
            value = node.get("datePublished") if isinstance(node, dict) else None
            if isinstance(value, str) and len(value) >= 7:
                try:
                    return int(value[0:4]), int(value[5:7])
                except ValueError:
                    continue
    return None


def scan(path, now_ym):
    html = path.read_text(encoding="utf-8", errors="replace")
    errors, warnings = [], []

    match = ISSUE.search(html)
    if not match:
        return ["no masthead issue line found"], []

    _vol, number, month_name, year = match.groups()
    number, year = int(number), int(year)
    if month_name not in MONTHS:
        return [f"unrecognized month name {month_name!r} in the issue line"], []
    stated = (year, MONTHS[month_name])
    label = f"No. {number} {month_name} {year}"

    expected_ym = issue_month(number)
    if expected_ym != stated:
        errors.append(
            f"issue line {label} is self-contradictory — "
            f"No. {number} is {NAMES[expected_ym[1]]} {expected_ym[0]}"
        )
        return errors, warnings

    if path.name in EVERGREEN:
        if now_ym is None:
            return errors, warnings
        current = issue_number(*now_ym)
        if number < current:
            warnings.append(
                f"evergreen page still reads {label} — "
                f"current issue is No. {current} {NAMES[now_ym[1]]} {now_ym[0]}"
            )
        elif number > current:
            errors.append(
                f"evergreen page reads {label}, which is ahead of the current "
                f"issue No. {current} {NAMES[now_ym[1]]} {now_ym[0]}"
            )
        return errors, warnings

    pub = published(html)
    if pub is None:
        warnings.append(
            f"{label} could not be checked — page carries no JSON-LD "
            f"datePublished and is not in the evergreen list"
        )
        return errors, warnings

    want = issue_number(*pub)
    if want != number:
        errors.append(
            f"issue line {label} disagrees with datePublished "
            f"{pub[0]}-{pub[1]:02d} — that month is No. {want} "
            f"{NAMES[issue_month(want)[1]]} {issue_month(want)[0]}"
        )

    return errors, warnings


def main():
    strict = "--strict" in sys.argv
    # The evergreen rule is time-dependent, not commit-dependent: the same tree
    # passes in September and warns in October. Keep it off the PR gate so a
    # merge never fails for a reason the branch did not cause.
    calendar = "--no-calendar" not in sys.argv and not strict
    root = Path(".")
    if "--path" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--path") + 1])

    today = datetime.now(timezone.utc)
    now_ym = (today.year, today.month) if calendar else None

    bad, warned, scanned = {}, {}, 0
    for path in sorted(root.rglob("*.html")):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        scanned += 1
        errors, warnings = scan(path, now_ym)
        if errors:
            bad[str(path)] = errors
        if warnings:
            warned[str(path)] = warnings

    current = issue_number(today.year, today.month)
    print(
        f"Issue line check — {scanned} pages scanned "
        f"(current issue: No. {current} {today.strftime('%B %Y')}"
        f"{'' if calendar else '; evergreen rule off'})"
    )

    if bad:
        print(f"\nERRORS ({len(bad)} pages)")
        for path in sorted(bad):
            print(f"  {path}")
            for line in bad[path]:
                print(f"      {line}")
    if warned:
        print(f"\nWARNINGS ({len(warned)} pages)")
        for path in sorted(warned):
            print(f"  {path}")
            for line in warned[path]:
                print(f"      {line}")
    if not bad and not warned:
        print("\nEvery issue line agrees with its page.")

    return 1 if (bad and strict) else 0


if __name__ == "__main__":
    sys.exit(main())
