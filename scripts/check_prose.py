#!/usr/bin/env python3
"""Check the mechanics of the prose on every Lawsuit Intelligencer page.

Why this exists. Before the October 21, 2026 update to ai-wrongful-death-docket
ran, the sentence lengths, paragraph lengths, JSON-LD and tag balance were all
measured by a one-off script outside the repo, because nothing inside it does.
Informer's check_consumer_readability.py covers this ground for that site, but
most of that script is consumer-site policy: the grade ceiling, the jargon
blocklist, the rule against telling a reader whether they have a claim, the CTA
set. None of that applies to a trade readership. What carries over is the
mechanical set, and that is all this file checks.

The page set is every top-level *.html file except 404.html. Listing pages
(index, archive, the interview hub) are checked for everything except prose
length, since card text is short by design and a long run there is a template
problem, not a writing one.

Rules
-----
ERROR  JSON-LD that does not parse. A broken schema block is silently dropped
       by every crawler, so the page loses its dates and author.
ERROR  Unbalanced <p>, <li>, <h2>, <h3>, <em>, <strong>, <blockquote> or
       <details>. Caught by hand twice in October after edits spliced a
       paragraph in two.
ERROR  Doubled sentence punctuation: a period, question mark or exclamation
       point directly after another, outside an ellipsis.
WARN   A sentence at or above SENTENCE_MAX words. Usually an enumeration that
       should be two sentences.
WARN   A prose block (<p>, <li>, <blockquote>, <dd>) at or above PARAGRAPH_MAX
       words. Split it where the subject changes.
WARN   A sentence that starts with "And". House rule since 9/16/26.
WARN   An ellipsis, typed or as the &hellip; entity, in body prose.
WARN   <title> over TITLE_MAX characters; meta description outside
       DESC_MIN to DESC_MAX.

Baseline
--------
The June and July articles predate the paragraph rule and carry blocks over
PARAGRAPH_MAX. They are not wrong, they are old. Pass --baseline to count only
findings in files changed on the current branch relative to origin/main (or the
ref named after it), so the PR gate measures the edit and not the archive. The
full report is still printed; only the exit code is affected.

Usage
-----
    python3 scripts/check_prose.py                      # report, exit 0
    python3 scripts/check_prose.py --strict             # exit 1 on any ERROR
    python3 scripts/check_prose.py --strict --baseline  # ERRORs in changed files only
    python3 scripts/check_prose.py --page ai-wrongful-death-docket.html
    python3 scripts/check_prose.py --path ../lawsuitintelligencer
"""

import html as htmllib
import json
import re
import subprocess
import sys
from pathlib import Path

SENTENCE_MAX = 30
PARAGRAPH_MAX = 90
TITLE_MAX = 60
DESC_MIN, DESC_MAX = 110, 160

SKIP = {"404.html"}
# Pages whose <main> is cards and lists rather than an article. Length rules
# are skipped; everything else runs.
LISTING = {"index.html", "archive.html", "interviews.html"}

BALANCED = ["p", "li", "h2", "h3", "em", "strong", "blockquote", "details"]
BLOCK_RE = re.compile(r"<(p|li|blockquote|dd)(?:\s[^>]*)?>(.*?)</\1>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
INLINE_RE = re.compile(r"</?(?:a|em|strong|span|cite|abbr|code|i|b|sup|sub|time)(?:\s[^>]*)?>")
ABBREV = {"no", "nos", "v", "vs", "mr", "mrs", "ms", "dr", "jr", "sr", "inc", "corp",
          "co", "ltd", "st", "u.s", "e.g", "i.e", "jan", "feb", "mar", "apr", "jun",
          "jul", "aug", "sept", "sep", "oct", "nov", "dec", "mdl", "jccp", "cal", "fla",
          "f.supp", "f", "cir", "dist", "hon", "esq", "p", "pp", "sec", "ch", "art"}


def strip(s):
    """Visible text of an HTML fragment, entities decoded, whitespace folded."""
    s = INLINE_RE.sub("", s)
    s = TAG_RE.sub(" ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def sentences(text):
    """Split prose into sentences without breaking on 'v.', 'No.', 'Oct.' and
    the other abbreviations case writing is full of."""
    out, start = [], 0
    for m in re.finditer(r"[.!?]+(?=\s+[\"'(\[]?[A-Z0-9]|\s*$)", text):
        prev = text[start:m.start()].rsplit(" ", 1)[-1].lower().strip("(\"'")
        if prev in ABBREV or re.fullmatch(r"[a-z]", prev):
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def main_region(page):
    m = re.search(r"<main[^>]*>(.*?)</main>", page, re.S)
    return m.group(1) if m else page


def body_region(page):
    """Prose region: article-body when the page has one, otherwise <main>. The
    'More from Lawsuit Intelligencer' grid and the footer are left out either
    way, since their card text belongs to other pages."""
    m = re.search(r'<div class="article-body">(.*?)</div>\s*(?:<(?:aside|section|nav|footer)|<div class="(?:related|more)|</article>)', page, re.S)
    if m:
        return m.group(1)
    return main_region(page)


def check(path):
    page = path.read_text(encoding="utf-8")
    errors, warnings = [], []
    name = path.name

    # JSON-LD
    for i, block in enumerate(re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S), 1):
        try:
            json.loads(block)
        except json.JSONDecodeError as e:
            errors.append(f"JSON-LD block {i} does not parse: {e.msg} at line {e.lineno}")

    # Tag balance, counted over the whole page
    for t in BALANCED:
        opened = len(re.findall(rf"<{t}(?:\s[^>]*)?>", page))
        closed = len(re.findall(rf"</{t}>", page))
        if opened != closed:
            errors.append(f"<{t}> opened {opened} times, closed {closed}")

    # Title and description
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    if m:
        title = strip(m.group(1))
        if len(title) > TITLE_MAX:
            warnings.append(f"<title> is {len(title)} characters (house limit {TITLE_MAX}): {title}")
    m = re.search(r'<meta name="description" content="(.*?)"', page, re.S)
    if m:
        desc = strip(m.group(1))
        if not DESC_MIN <= len(desc) <= DESC_MAX:
            warnings.append(f"meta description is {len(desc)} characters (house range {DESC_MIN}-{DESC_MAX})")

    # Prose rules
    region = body_region(page)
    text_all = strip(region)
    for m in re.finditer(r"[.!?][.!?]+", text_all):
        if m.group() == "...":
            continue
        ctx = text_all[max(0, m.start() - 40): m.end() + 10]
        errors.append(f"doubled sentence punctuation: “{ctx}”")
    if "..." in text_all or "…" in text_all:
        warnings.append("ellipsis in body prose")

    if name not in LISTING:
        for m in BLOCK_RE.finditer(region):
            tag, inner = m.group(1), m.group(2)
            # A <li> that wraps a nested block is a container, not prose.
            if tag == "li" and re.search(r"<(p|ul|ol)\b", inner):
                continue
            text = strip(inner)
            if not text:
                continue
            n = len(text.split())
            if n >= PARAGRAPH_MAX:
                warnings.append(f"<{tag}> of {n} words (limit {PARAGRAPH_MAX}): “{text[:70]}…”")
            for s in sentences(text):
                w = len(s.split())
                if w >= SENTENCE_MAX:
                    warnings.append(f"sentence of {w} words: “{s[:80]}…”")
                if re.match(r"And\b", s):
                    warnings.append(f"sentence starts with And: “{s[:60]}…”")

    return errors, warnings


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


def main_region(page):
    m = re.search(r"<main[^>]*>(.*?)</main>", page, re.S)
    return m.group(1) if m else page


def body_region(page):
    """Prose region: article-body when the page has one, otherwise <main>. The
    'More from Lawsuit Intelligencer' grid and the footer are left out either
    way, since their card text belongs to other pages."""
    m = re.search(r'<div class="article-body">(.*?)</div>\s*(?:<(?:aside|section|nav|footer)|<div class="(?:related|more)|</article>)', page, re.S)
    if m:
        return m.group(1)
    return main_region(page)


def check(path):
    page = path.read_text(encoding="utf-8")
    errors, warnings = [], []
    name = path.name

    # JSON-LD
    for i, block in enumerate(re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S), 1):
        try:
            json.loads(block)
        except json.JSONDecodeError as e:
            errors.append(f"JSON-LD block {i} does not parse: {e.msg} at line {e.lineno}")

    # Tag balance, counted over the whole page
    for t in BALANCED:
        opened = len(re.findall(rf"<{t}(?:\s[^>]*)?>", page))
        closed = len(re.findall(rf"</{t}>", page))
        if opened != closed:
            errors.append(f"<{t}> opened {opened} times, closed {closed}")

    # Title and description
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    if m:
        title = strip(m.group(1))
        if len(title) > TITLE_MAX:
            warnings.append(f"<title> is {len(title)} characters (house limit {TITLE_MAX}): {title}")
    m = re.search(r'<meta name="description" content="(.*?)"', page, re.S)
    if m:
        desc = strip(m.group(1))
        if not DESC_MIN <= len(desc) <= DESC_MAX:
            warnings.append(f"meta description is {len(desc)} characters (house range {DESC_MIN}-{DESC_MAX})")

    # Prose rules
    region = body_region(page)
    text_all = strip(region)
    for m in re.finditer(r"[.!?][.!?]+", text_all):
        if m.group() == "...":
            continue
        ctx = text_all[max(0, m.start() - 40): m.end() + 10]
        errors.append(f"doubled sentence punctuation: “{ctx}”")
    if "..." in text_all or "…" in text_all:
        warnings.append("ellipsis in body prose")

    if name not in LISTING:
        for m in BLOCK_RE.finditer(region):
            tag, inner = m.group(1), m.group(2)
            # A <li> that wraps a nested block is a container, not prose.
            if tag == "li" and re.search(r"<(p|ul|ol)\b", inner):
                continue
            text = strip(inner)
            if not text:
                continue
            n = len(text.split())
            if n >= PARAGRAPH_MAX:
                warnings.append(f"<{tag}> of {n} words (limit {PARAGRAPH_MAX}): “{text[:70]}…”")
            for s in sentences(text):
                w = len(s.split())
                if w >= SENTENCE_MAX:
                    warnings.append(f"sentence of {w} words: “{s[:80]}…”")
                if re.match(r"And\b", s):
                    warnings.append(f"sentence starts with And: “{s[:60]}…”")

    return errors, warnings


def changed_files(root, base):
    """Files changed on this branch relative to base, as names. Empty when git
    cannot answer, in which case --baseline falls back to the whole tree."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "diff", "--name-only", base],
                capture_output=True, text=True, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
    return {Path(line.strip()).name for line in out.splitlines() if line.strip()}


def main():
    argv = sys.argv[1:]
    strict = "--strict" in argv
    root = Path(argv[argv.index("--path") + 1]) if "--path" in argv else Path(".")
    only = argv[argv.index("--page") + 1] if "--page" in argv else None
    base = None
    if "--baseline" in argv:
        i = argv.index("--baseline")
        base = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "origin/main"

    paths = sorted(p for p in root.glob("*.html") if p.name not in SKIP)
    if only:
        paths = [p for p in paths if p.name == Path(only).name]
        if not paths:
            print(f"No such page: {only}")
            sys.exit(2)

    gated = None
    if base:
        gated = changed_files(root, base)
        if gated is None:
            print(f"Note: git could not diff against {base}; gating on every page instead.")

    bad, warned = {}, {}
    for p in paths:
        e, w = check(p)
        if e:
            bad[p.name] = e
        if w:
            warned[p.name] = w

    print(f"Prose check — {len(paths)} pages scanned "
          f"(sentence {SENTENCE_MAX}+, block {PARAGRAPH_MAX}+, title {TITLE_MAX}, description {DESC_MIN}-{DESC_MAX})")
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
        print("\nEvery page is within the house limits.")

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
