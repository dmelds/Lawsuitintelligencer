#!/usr/bin/env python3
"""Check that updates and corrections on Lawsuit Intelligencer pages follow the
corrections policy on editorial-standards.html.

The policy, in the words of that page: each substantive correction is noted at
the foot of the piece, dated, and describes what was changed. If a correction
materially changes the meaning of a piece, it is said at the top as well.

The house markup that carries this, from the pages that have used it:

    <p class="article-meta">Updated October 21, 2026, with ... and a correction.</p>
    <p><strong>Update, October 21, 2026:</strong> ...</p>
    <p><em>Correction, October 21, 2026: As first published, this piece said ... </em></p>

The first is the header line, under the byline. The second is an inline update
block in the body. The third is the foot note the policy requires. Whether a
correction is material is an editorial call and is not checked; whether the
three pieces of markup agree with each other and with the JSON-LD is.

check_date_consistency.py already holds the header "Updated" date to
dateModified. This script checks what sits around it.

Rules
-----
ERROR  The header Updated line says the piece was corrected ("correct",
       "correction", "corrects") and the body has no dated Correction note.
       The policy puts the note at the foot; a mention in the header is not it.
ERROR  The body has a Correction note and the header has no Updated line, or
       the Updated line does not mention the correction. A reader who stops at
       the byline should know the piece was corrected.
ERROR  A Correction note's date is earlier than datePublished or later than
       dateModified.
ERROR  An inline "Update, <date>:" block carries a date later than
       dateModified. The page was edited on a day the schema does not admit.
ERROR  A Correction note that gives no date, or an inline Update block that
       gives no date.
WARN   An inline "Update, <date>:" block exists and the header has no Updated
       line. The pfas piece carries both; one without the other reads as a
       draft.
WARN   A Correction note is not in the last three prose blocks of the article
       body. The policy says "at the foot".
WARN   A Correction note under 15 words. The policy says it describes what was
       changed; a bare "corrected a date" does not.

Baseline
--------
ai-chat-logs-deposition.html, updated September 21, 2026, says in its header
that it corrected the characterization of two rulings as a split, and carries
no foot note. That predates this check. Pass --baseline so the PR gate counts
only errors in files changed on the branch relative to origin/main (or the ref
named after it); the full report still prints.

Usage
-----
    python3 scripts/check_corrections.py             # report, exit 0
    python3 scripts/check_corrections.py --strict    # exit 1 on any ERROR
    python3 scripts/check_corrections.py --strict --baseline   # ERRORs in changed files only
    python3 scripts/check_corrections.py --page ai-wrongful-death-docket.html
    python3 scripts/check_corrections.py --path ../lawsuitintelligencer
"""

import html as htmllib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SKIP = {"404.html", "index.html", "archive.html", "interviews.html", "about.html",
        "editorial-standards.html", "contribute.html"}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                     r"(?:&nbsp;|\s)+(\d{1,2}),(?:&nbsp;|\s)+(\d{4})")
TAG_RE = re.compile(r"<[^>]+>")


def strip(s):
    s = TAG_RE.sub(" ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(s).replace("\xa0", " ")).strip()


def parse_date(text):
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    except ValueError:
        return None


def iso(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def schema_dates(page):
    pub = mod = None
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@type") in ("Article", "NewsArticle", "AnalysisNewsArticle", "BlogPosting"):
                    pub = pub or iso(node.get("datePublished"))
                    mod = mod or iso(node.get("dateModified"))
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return pub, mod


def check(path):
    page = path.read_text(encoding="utf-8")
    errors, warnings = [], []

    pub, mod = schema_dates(page)

    # Header "Updated" line(s)
    header = [strip(m) for m in re.findall(r'<p class="article-meta">(Updated .*?)</p>', page, re.S)]
    header_text = " ".join(header)
    header_says_corrected = bool(re.search(r"\bcorrect(?:s|ed|ion|ions)?\b", header_text, re.I))

    # Body region
    m = re.search(r'<div class="article-body">(.*?)</div>', page, re.S)
    body = m.group(1) if m else (re.search(r"<main[^>]*>(.*?)</main>", page, re.S) or re.search(r"(.*)", page, re.S)).group(1)
    blocks = re.findall(r"<p(?:\s[^>]*)?>.*?</p>", body, re.S)

    # Correction notes: a <p> whose visible text starts with "Correction"
    corrections = []
    for i, b in enumerate(blocks):
        t = strip(b)
        if re.match(r"Corrections?\b", t):
            corrections.append((i, t))

    # Inline update blocks
    updates = []
    for b in blocks:
        t = strip(b)
        mm = re.match(r"Update,?\s+(.*?):", t)
        if mm and re.match(r"<p(?:\s[^>]*)?>\s*<strong>", b):
            updates.append(t)

    if header_says_corrected and not corrections:
        errors.append("header Updated line says the piece was corrected, but the body has no Correction note")
    if corrections and not header:
        errors.append("body has a Correction note but the header has no Updated line")
    elif corrections and not header_says_corrected:
        errors.append("body has a Correction note but the header Updated line does not mention it")

    for i, t in corrections:
        d = parse_date(t)
        if not d:
            errors.append(f"Correction note gives no date: “{t[:60]}…”")
        else:
            if pub and d < pub:
                errors.append(f"Correction dated {d.isoformat()} is earlier than datePublished {pub.isoformat()}")
            if mod and d > mod:
                errors.append(f"Correction dated {d.isoformat()} is later than dateModified {mod.isoformat()}")
        if i < len(blocks) - 3:
            warnings.append(f"Correction note is block {i + 1} of {len(blocks)}; the policy puts it at the foot")
        if len(t.split()) < 15:
            warnings.append(f"Correction note is {len(t.split())} words; the policy says it describes what was changed")

    for t in updates:
        d = parse_date(t)
        if not d:
            errors.append(f"inline Update block gives no date: “{t[:60]}…”")
        elif mod and d > mod:
            errors.append(f"inline Update dated {d.isoformat()} is later than dateModified {mod.isoformat()}")
        if pub and d and d < pub:
            errors.append(f"inline Update dated {d.isoformat()} is earlier than datePublished {pub.isoformat()}")
    if updates and not header:
        warnings.append("body has an inline Update block but the header has no Updated line")

    return errors, warnings, bool(header or corrections or updates)


def changed_files(root, base):
    """Files changed on this branch relative to base, as names. None when git
    cannot answer, in which case --baseline falls back to the whole tree."""
    for args in ([f"{base}...HEAD"], [base]):
        try:
            out = subprocess.run(["git", "-C", str(root), "diff", "--name-only", *args],
                                 capture_output=True, text=True, check=True).stdout
            return {Path(line.strip()).name for line in out.splitlines() if line.strip()}
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def main():
    argv = sys.argv[1:]
    strict = "--strict" in argv
    root = Path(argv[argv.index("--path") + 1]) if "--path" in argv else Path(".")
    only = argv[argv.index("--page") + 1] if "--page" in argv else None
    base = None
    if "--baseline" in argv:
        i = argv.index("--baseline")
        base = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "origin/main"
    gated = None
    if base:
        gated = changed_files(root, base)
        if gated is None:
            print(f"Note: git could not diff against {base}; gating on every page instead.")

    paths = sorted(p for p in root.glob("*.html") if p.name not in SKIP)
    if only:
        paths = [p for p in paths if p.name == Path(only).name]
        if not paths:
            print(f"No such page: {only}")
            sys.exit(2)

    bad, warned, touched = {}, {}, 0
    for p in paths:
        e, w, has = check(p)
        touched += has
        if e:
            bad[p.name] = e
        if w:
            warned[p.name] = w

    print(f"Corrections check — {len(paths)} pages scanned, {touched} carry an update or correction")
    if bad:
        print(f"\nERRORS ({len(bad)} pages)")
        for n in sorted(bad):
            print(f"  {n}")
            for line in bad[n]:
                print(f"      {line}")
    if warned:
        print(f"\nWARNINGS ({len(warned)} pages)")
        for n in sorted(warned):
            print(f"  {n}")
            for line in warned[n]:
                print(f"      {line}")
    if not bad and not warned:
        print("\nEvery update and correction agrees with its page and the policy.")
    if strict:
        failing = set(bad)
        if gated is not None:
            failing &= gated
            skipped = set(bad) - failing
            if skipped:
                print(f"\nBaseline: {len(skipped)} page(s) with errors are unchanged on this branch and do not gate: {', '.join(sorted(skipped))}")
        if failing:
            print(f"\nFAIL: {len(failing)} page(s) with errors: {', '.join(sorted(failing))}")
            sys.exit(1)
        print("\nPASS: no errors" + (" in changed pages" if gated is not None else ""))


if __name__ == "__main__":
    main()
