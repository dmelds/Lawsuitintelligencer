#!/usr/bin/env python3
"""Check that every date signal on a Lawsuit Intelligencer page agrees with the page.

Ported from the Informer checker of the same name. The rules about month stamps
in titles and metadata carry over unchanged. The visible-date rules do not, and
the difference is the reason this file exists separately rather than being
copied across.

On Informer a visible date means currency: "Last updated July 3, 2026" tells a
reader the page is being maintained, and it is compared against dateModified.
Intelligencer carries two different visible dates that must not be confused:

    <p class="article-meta">By David Meldofsky &middot; May 30, 2026 &middot; 11 min read</p>
    <p class="article-meta">Updated September 18, 2026, with the JPML's report...</p>

The first is a PUBLICATION date. It belongs to datePublished, it is an archive
fact, and it never moves. The second is a CURRENCY date and belongs to
dateModified. Checking the byline against dateModified, which is what a naive
port would do, flags every correctly-maintained article on the site: a piece
published in May and updated in September is supposed to show May in the byline.

Listing cards are the other trap. The index, the interviews hub and the
"More from Lawsuit Intelligencer" block at the foot of every article each carry
<p class="meta"> lines dating OTHER articles. Reading a date out of one of those
and attributing it to the host page produces a confident false finding, so this
script reads visible dates only from <p class="article-meta"> and from the
*-updated classes, never from a listing.

Rules
-----
ERROR  A byline publication date disagrees with JSON-LD datePublished.
ERROR  An "Updated"/"Last updated" date disagrees with JSON-LD dateModified.
ERROR  dateModified is earlier than datePublished.
ERROR  A Month YYYY stamp in the title, og/twitter tags, meta description, h1 or
       a self-dating JSON-LD node is NEWER than dateModified. The page claims a
       currency month it was never edited in. Blocks the merge under --strict.
ERROR  The <title> stamp disagrees with the <h1> stamp or the og:title stamp.
WARN   A title or h1 stamp is OLDER than dateModified. The page was edited and
       the stamp did not move. The stamp may refer to an event rather than to
       currency, so this never blocks.
WARN   A visible stamp gives a bare month with no day and the page carries no
       JSON-LD dates to check it against. contribute.html is the live example.
WARN   The <title> stamp is older than the month the check is running in, even
       though the page agrees with itself. Report-only and never on a PR: the
       calendar turning over is not something the branch did.

The masthead issue line is deliberately out of scope. It is governed by
check_issue_line.py, which knows that an article's issue line is frozen and an
evergreen page's advances.

Usage
-----
    python3 check_date_consistency.py            # report, exit 0
    python3 check_date_consistency.py --strict   # exit 1 on any ERROR
    python3 check_date_consistency.py --path .   # scan root (default .)
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

STAMP = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(20\d{2})\b")
FULLDATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s*(20\d{2})\b")

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
META = re.compile(r"<meta\b[^>]*>", re.S | re.I)
ATTR = re.compile(r'(\w[\w:-]*)\s*=\s*"([^"]*)"', re.S)
LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
TAGS = re.compile(r"<[^>]+>")

# Visible date sources. Only these two. <p class="meta"> is excluded on purpose:
# it is the listing-card class and its dates belong to other articles.
ARTICLE_META = re.compile(r'<p class="article-meta">(.*?)</p>', re.S | re.I)
UPDATED_CLASS = re.compile(r'<p class="[a-z-]*updated">(.*?)</p>', re.S | re.I)

# The masthead carries "Month YYYY" on every page. It is check_issue_line.py's
# business, and it must never be read as a freshness stamp here.
MASTHEAD = re.compile(
    r"Vol\.\s*[IVXL]+\s*(?:&middot;|·)\s*No\.\s*\d+\s*"
    r"(?:&middot;|·)\s*[A-Z][a-z]+\s+20\d{2}")


def text(raw):
    return " ".join(TAGS.sub(" ", raw or "").split())


def stamps(value):
    return [(int(y), MONTHS[m]) for m, y in STAMP.findall(value or "")]


def label(ym):
    return f"{NAMES[ym[1]]} {ym[0]}"


def metas(html):
    out = {}
    for tag in META.findall(html):
        attrs = dict(ATTR.findall(tag))
        key = attrs.get("name") or attrs.get("property")
        if key and "content" in attrs:
            out.setdefault(key.lower(), attrs["content"])
    return out


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def jsonld(html):
    """Return (datePublished, dateModified, [(field, value), ...])."""
    published = modified = None
    fields = []
    for block in LD.findall(html):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in walk(data):
            if not isinstance(node, dict):
                continue
            dp, dm = node.get("datePublished"), node.get("dateModified")
            if isinstance(dp, str) and not published:
                published = dp
            if isinstance(dm, str) and not modified:
                modified = dm
            # Only read headline/description off a node that dates itself. A
            # listing page carries an ItemList describing other articles, and
            # those headlines date those articles rather than this page.
            if not isinstance(dm, str):
                continue
            for key in ("headline", "description"):
                value = node.get(key)
                if isinstance(value, str):
                    fields.append((f"ld:{key}", value))
    return published, modified, fields


def full_date(value):
    m = FULLDATE.search(value or "")
    return (int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))) if m else None


def iso_ymd(value):
    m = re.match(r"(20\d{2})-(\d{2})-(\d{2})", value or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def iso_ym(value):
    m = re.match(r"(20\d{2})-(\d{2})", value or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def visible_dates(html):
    """Classify every visible date on the page.

    Returns (published, updated, bare) where published and updated are lists of
    (ymd, raw) and bare is a list of raw strings carrying a month with no day.
    """
    published, updated, bare = [], [], []
    blocks = [text(b) for b in ARTICLE_META.findall(html)]
    blocks += [text(b) for b in UPDATED_CLASS.findall(html)]

    for raw in blocks:
        if MASTHEAD.search(raw):
            continue
        ymd = full_date(raw)
        is_update = re.search(r"\b(last\s+)?updated\b", raw, re.I)
        if ymd:
            (updated if is_update else published).append((ymd, raw))
        elif stamps(raw):
            bare.append(raw)
    return published, updated, bare


def scan(path, now_ym=None):
    html = path.read_text(encoding="utf-8", errors="replace")
    m = TITLE.search(html)
    if not m:
        return [], []
    title = text(m.group(1))
    h1m = H1.search(html)
    h1 = text(h1m.group(1)) if h1m else ""
    mt = metas(html)
    dp_raw, dm_raw, ld_fields = jsonld(html)
    dp_full, dm_full = iso_ymd(dp_raw), iso_ymd(dm_raw)
    dm = iso_ym(dm_raw)

    fields = [("title", title), ("h1", h1)]
    for key in ("description", "og:title", "og:description",
                "twitter:title", "twitter:description"):
        if key in mt:
            fields.append((key, mt[key]))
    fields.extend(ld_fields)

    errors, warnings = [], []

    if dp_full and dm_full and dm_full < dp_full:
        errors.append(
            f"dateModified {dm_raw} is earlier than datePublished {dp_raw}"
        )

    if dm:
        for name, value in fields:
            for ym in stamps(value):
                if ym > dm:
                    errors.append(
                        f"{name} claims {label(ym)} but dateModified is "
                        f"{dm_raw} ({label(dm)})"
                    )

    t_stamps = stamps(title)
    if t_stamps:
        newest = max(t_stamps)
        for name, value in (("h1", h1), ("og:title", mt.get("og:title", ""))):
            other = stamps(value)
            if other and max(other) != newest:
                errors.append(
                    f"title says {label(newest)} but {name} says {label(max(other))}"
                )
        if dm and newest < dm:
            warnings.append(
                f"title stamp {label(newest)} is older than dateModified "
                f"{dm_raw} — page was edited, stamp was not"
            )
        if now_ym and newest < now_ym:
            months = (now_ym[0] - newest[0]) * 12 + (now_ym[1] - newest[1])
            warnings.append(
                f"title stamp {label(newest)} is {months} month"
                f"{'s' if months != 1 else ''} behind the current month "
                f"({label(now_ym)}) — the page still reads as current to the "
                f"checker but not to a searcher"
            )

    pub_visible, upd_visible, bare = visible_dates(html)

    # A byline date is a publication fact. It answers to datePublished and to
    # nothing else, least of all to dateModified.
    for ymd, raw in pub_visible:
        if dp_full and ymd != dp_full:
            errors.append(
                f"byline shows {NAMES[ymd[1]]} {ymd[2]}, {ymd[0]} but "
                f"datePublished is {dp_raw}"
            )
        elif not dp_raw:
            warnings.append(
                f"byline shows a date ({raw[:60]}) but the page carries no "
                f"JSON-LD datePublished to check it against"
            )

    # An update note is a currency claim and answers to dateModified.
    for ymd, raw in upd_visible:
        if dm_full and ymd != dm_full:
            when = f"{NAMES[ymd[1]]} {ymd[2]}, {ymd[0]}"
            if ymd > dm_full:
                errors.append(
                    f"update note says {when} but dateModified is {dm_raw} — "
                    f"the page shows an edit date it was never edited on"
                )
            else:
                errors.append(
                    f"update note says {when} but dateModified is {dm_raw} — "
                    f"the page was edited again and the note did not move"
                )
        elif not dm_raw:
            warnings.append(
                f"update note shows a date ({raw[:60]}) but the page carries "
                f"no JSON-LD dateModified to check it against"
            )

    for raw in bare:
        if not dm_raw and not dp_raw:
            warnings.append(
                f"visible stamp \"{raw[:60]}\" gives a month with no day and "
                f"the page carries no JSON-LD dates — nothing can verify it"
            )

    h_stamps = stamps(h1)
    if h_stamps and dm and max(h_stamps) < dm and not t_stamps:
        warnings.append(
            f"h1 stamp {label(max(h_stamps))} is older than dateModified {dm_raw}"
        )

    return errors, warnings


def main():
    strict = "--strict" in sys.argv
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

    print(
        f"Date consistency check — {scanned} pages scanned "
        f"(build month: {today.strftime('%B %Y')}"
        f"{'' if calendar else '; calendar rule off'})"
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
        print("\nAll date signals consistent.")

    return 1 if (bad and strict) else 0


if __name__ == "__main__":
    sys.exit(main())
