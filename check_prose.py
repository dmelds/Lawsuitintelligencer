#!/usr/bin/env python3
"""Check the mechanics of the prose on every Lawsuit Intelligencer page, and
check that the listing pages agree with the articles they list.

Why this exists. Before the October 21, 2026 update to ai-wrongful-death-docket
ran, the sentence lengths, paragraph lengths, JSON-LD and tag balance were all
measured by a one-off script outside the repo, because nothing inside it does.
Informer's check_consumer_readability.py covers this ground for that site, but
most of that script is consumer-site policy: the grade ceiling, the jargon
blocklist, the rule against telling a reader whether they have a claim, the CTA
set. None of that applies to a trade readership. What carries over is the
mechanical set, and that is what the prose rules check.

The listing rules were added October 22, 2026, after the Rothrock interview.
That page went through four rounds of dek and read-time edits, and each round
meant re-editing index.html, archive.html and interviews.html by hand to keep
the rows in step. Nothing in the repo noticed when they drifted. The read time
was also wrong on the first handover (7 min against 2,000 words) and only a
one-off word count caught it. Both are now checked here.

The page set is every top-level *.html file except 404.html. Listing pages
(index, archive, the interview hub) are checked for everything except prose
length, since card text is short by design and a long run there is a template
problem, not a writing one.

Prose rules
-----------
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
WARN   The "N min read" in the byline is more than one minute off the body
       word count at READ_WPM words a minute. The interview series runs at
       about 230; older analysis pieces vary and will warn until re-stamped.
WARN   The intro (article-body above the first <h2>) runs to more than
       INTRO_MAX_PARAS paragraphs, or carries a paragraph under INTRO_MIN
       words that is not the closing sign-off line. Added after the
       Rothrock intro went out as ten paragraphs, four of them two-sentence
       stubs, and had to be re-cut to seven. Approved intros on the site
       run three to seven paragraphs of roughly 45 to 90 words, plus the
       sign-off ("Her answers run below. The section headings are mine.").

Listing rules (an article is any page whose JSON-LD @type is Article)
----------------------------------------------------------------------
ERROR  An article has no row on archive.html, or its row sits under the
       wrong month heading. STANDING pages are exempt: they are the index's
       Featured and Tracker sections, not entries in the run.
ERROR  An Interview article has no row on interviews.html.
ERROR  A row's <time datetime> (index, archive) or its spelled-out date
       (interviews) disagrees with the article's datePublished.
ERROR  A row's "N min read" (index, archive) disagrees with the article's
       own byline.
ERROR  index.html Recent does not hold exactly INDEX_ROWS rows, the first
       row is not class="lead", or the rows are not the INDEX_ROWS newest
       articles by datePublished.
WARN   index.html Recent rows are out of date order.
WARN   A row's title is neither the article's <h1> nor its opening words.
       Rows shorten long titles on purpose; a title that has moved on
       without its rows is the case this catches.

Listing findings are charged to the ARTICLE they concern (plus index.html
for the structural rules), so a new or edited article gates its own PR even
when the listing page was not touched, which is exactly the case where the
row was forgotten.

Readability report
------------------
For every article in scope under --page or --baseline (or every article,
with --report), a line of words, stated read time, computed read time,
Flesch-Kincaid grade and the intro's paragraph shape (count and the words
in each), so the shape of the opening is visible at handover. The grade is reported, never gated: this is a trade
readership and the standing rule is to report it with the file, not to cap
it.

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
    python3 scripts/check_prose.py --report             # readability table for every article
    python3 scripts/check_prose.py --path ../lawsuitintelligencer
"""

import html as htmllib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SENTENCE_MAX = 30
PARAGRAPH_MAX = 90
TITLE_MAX = 60
DESC_MIN, DESC_MAX = 110, 160
READ_WPM = 230
READ_TOLERANCE = 1
INTRO_MIN = 30
INTRO_MAX_PARAS = 7
INDEX_ROWS = 10

SKIP = {"404.html"}
# Pages whose <main> is cards and lists rather than an article. Length rules
# are skipped; everything else runs.
LISTING = {"index.html", "archive.html", "interviews.html"}
# Articles that live in the index's Featured and Tracker sections and are
# updated in place. They are not entries in the dated run, so they are not
# expected on archive.html or in the index Recent list.
STANDING = {"mass-tort-map", "state-ai-enforcement-tracker"}

BALANCED = ["p", "li", "h2", "h3", "em", "strong", "blockquote", "details"]
BLOCK_RE = re.compile(r"<(p|li|blockquote|dd)(?:\s[^>]*)?>(.*?)</\1>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
INLINE_RE = re.compile(r"</?(?:a|em|strong|span|cite|abbr|code|i|b|sup|sub|time)(?:\s[^>]*)?>")
ROW_RE = re.compile(
    r'<li(?P<attrs>[^>]*)>\s*<div>\s*<h3><a href="(?P<slug>[^"]+)">(?P<title>.*?)</a></h3>\s*'
    r'<p class="dek">(?P<dek>.*?)</p>\s*<p class="meta">(?P<meta>.*?)</p>', re.S)
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
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


def plain(s):
    """strip(), then straight quotes, for comparing a row to its article."""
    return strip(s).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


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


def syllables(word):
    word = word.lower()
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", word)
    word = re.sub(r"^y", "", word)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", word)))


def fk_grade(text):
    """Flesch-Kincaid grade of a prose run. Returns None when there is no
    prose to measure."""
    sents = sentences(text)
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    if not sents or not words:
        return None
    syl = sum(syllables(w) for w in words)
    return 0.39 * len(words) / len(sents) + 11.8 * syl / len(words) - 15.59


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


def jsonld(page):
    """Every JSON-LD node on the page, @graph flattened. Parse errors are
    reported by check(); here they simply yield nothing."""
    nodes = []
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes.extend(d.get("@graph", [d]) if isinstance(d, dict) else [])
    return nodes


def article_facts(path, page):
    """None for a non-article; otherwise the facts the listing rows must
    agree with."""
    node = next((n for n in jsonld(page) if n.get("@type") == "Article"), None)
    if not node:
        return None
    h1 = re.search(r"<h1>(.*?)</h1>", page, re.S)
    eyebrow = re.search(r'class="article-eyebrow">(.*?)</p>', page, re.S)
    meta = re.search(r'class="article-meta">(.*?)</p>', page, re.S)
    read = re.search(r"(\d+)\s*min read", meta.group(1)) if meta else None
    return {
        "slug": path.stem,
        "title": plain(h1.group(1)) if h1 else "",
        "eyebrow": strip(eyebrow.group(1)) if eyebrow else "",
        "published": node.get("datePublished", ""),
        "read": int(read.group(1)) if read else None,
    }


def stated_read_time_warning(page):
    """The byline's 'N min read' against the body word count."""
    meta = re.search(r'class="article-meta">(.*?)</p>', page, re.S)
    read = re.search(r"(\d+)\s*min read", meta.group(1)) if meta else None
    if not read:
        return None
    words = len(strip(body_region(page)).split())
    expected = max(1, round(words / READ_WPM))
    stated = int(read.group(1))
    if abs(stated - expected) > READ_TOLERANCE:
        return f"byline says {stated} min read; body is {words:,} words, about {expected} min at {READ_WPM} wpm"
    return None


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
        w = stated_read_time_warning(page)
        if w:
            warnings.append(w)
        warnings.extend(intro_warnings(page))

    return errors, warnings


def intro_paragraphs(page):
    """Word counts of the intro paragraphs: article-body up to the first
    <h2>. Empty for pages without an article-body."""
    m = re.search(r'<div class="article-body">(.*?)(?:<h2>|</div>)', page, re.S)
    if not m:
        return []
    return [len(strip(p).split()) for p in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", m.group(1), re.S) if strip(p)]


def intro_warnings(page):
    counts = intro_paragraphs(page)
    if len(counts) < 2 or "<h2>" not in body_region(page):
        return []
    body = counts[:-1] if counts[-1] < INTRO_MIN else counts  # sign-off line is exempt
    out = []
    if len(body) > INTRO_MAX_PARAS:
        out.append(f"intro runs to {len(body)} paragraphs before the first <h2> (house shape is {INTRO_MAX_PARAS} or fewer): {'/'.join(map(str, counts))} words")
    short = [n for n in body if n < INTRO_MIN]
    if short:
        out.append(f"intro has {len(short)} paragraph(s) under {INTRO_MIN} words: {'/'.join(map(str, counts))} words")
    return out


def rows_of(page):
    """Listing rows as dicts, in page order, with the month heading (archive)
    each row sits under."""
    out, month = [], None
    for m in re.finditer(r"<h2>([A-Z][a-z]+ \d{4})</h2>|" + ROW_RE.pattern, page, re.S):
        if m.group(1):
            month = m.group(1)
            continue
        meta = m.group("meta")
        dt = re.search(r'datetime="(\d{4}-\d{2}-\d{2})"', meta)
        spelled = re.search(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})", strip(meta))
        if spelled and spelled.group(1) in MONTHS:
            spelled_iso = f"{spelled.group(3)}-{MONTHS.index(spelled.group(1)) + 1:02d}-{int(spelled.group(2)):02d}"
        else:
            spelled_iso = None
        read = re.search(r"(\d+)\s*min read", meta)
        out.append({
            "slug": m.group("slug"),
            "title": plain(m.group("title")),
            "lead": 'class="lead"' in m.group("attrs"),
            "date": dt.group(1) if dt else spelled_iso,
            "read": int(read.group(1)) if read else None,
            "month": month,
        })
    return out


def title_agrees(row_title, h1):
    if row_title == h1:
        return True
    # Rows shorten on purpose: the row may be the title's opening words, cut
    # at a colon, period or the end of the row.
    head = re.split(r"[:.?]", h1, maxsplit=1)[0].strip()
    return row_title.rstrip(".") in {h1.rstrip("."), head.rstrip(".")}


def check_listings(root, articles):
    """Listing findings keyed by the page they are charged to."""
    errors, warnings = {}, {}

    def err(name, msg):
        errors.setdefault(name, []).append(msg)

    def warn(name, msg):
        warnings.setdefault(name, []).append(msg)

    pages = {}
    for n in LISTING:
        p = root / n
        if p.exists():
            pages[n] = rows_of(p.read_text(encoding="utf-8"))

    def row_checks(listing, row, art):
        page = f"{art['slug']}.html"
        if row["date"] and art["published"] and row["date"] != art["published"]:
            err(page, f"{listing} row is dated {row['date']}; the page's datePublished is {art['published']}")
        if listing != "interviews.html" and row["read"] and art["read"] and row["read"] != art["read"]:
            err(page, f"{listing} row says {row['read']} min read; the page byline says {art['read']}")
        if art["title"] and not title_agrees(row["title"], art["title"]):
            warn(page, f"{listing} row title “{row['title']}” is neither the <h1> nor its opening words: “{art['title']}”")

    run = [a for a in articles.values() if a["slug"] not in STANDING and a["published"]]

    # archive: every article in the run, under its month
    if "archive.html" in pages:
        by_slug = {r["slug"]: r for r in pages["archive.html"]}
        for art in run:
            row = by_slug.get(art["slug"])
            if not row:
                err(f"{art['slug']}.html", "no row on archive.html")
                continue
            row_checks("archive.html", row, art)
            y, mo, _ = art["published"].split("-")
            want = f"{MONTHS[int(mo) - 1]} {y}"
            if row["month"] and row["month"] != want:
                err(f"{art['slug']}.html", f"archive.html row sits under {row['month']}; datePublished puts it in {want}")

    # interviews: every Interview article, with its spelled-out date
    if "interviews.html" in pages:
        by_slug = {r["slug"]: r for r in pages["interviews.html"]}
        for art in run:
            if not art["eyebrow"].startswith("Interview"):
                continue
            row = by_slug.get(art["slug"])
            if not row:
                err(f"{art['slug']}.html", "no row on interviews.html")
                continue
            row_checks("interviews.html", row, art)

    # index: exactly INDEX_ROWS, lead first, the newest in the run
    if "index.html" in pages:
        rows = pages["index.html"]
        if len(rows) != INDEX_ROWS:
            err("index.html", f"Recent holds {len(rows)} rows; the rule is {INDEX_ROWS}")
        if rows and not rows[0]["lead"]:
            err("index.html", "first Recent row is not class=\"lead\"")
        if sum(r["lead"] for r in rows) > 1:
            err("index.html", "more than one Recent row is class=\"lead\"")
        newest = sorted(run, key=lambda a: a["published"], reverse=True)[:INDEX_ROWS]
        want = {a["slug"] for a in newest}
        have = {r["slug"] for r in rows}
        for slug in sorted(want - have):
            err(f"{slug}.html", f"not on index.html Recent, but it is among the {INDEX_ROWS} newest by datePublished")
        for slug in sorted(have - want):
            err("index.html", f"Recent carries {slug}, which is outside the {INDEX_ROWS} newest by datePublished")
        dates = [r["date"] for r in rows if r["date"]]
        if dates != sorted(dates, reverse=True):
            warn("index.html", "Recent rows are not in date order, newest first")
        for row in rows:
            art = articles.get(row["slug"])
            if art:
                row_checks("index.html", row, art)

    return errors, warnings


def readability_line(path, page):
    body = strip(body_region(page))
    words = len(body.split())
    meta = re.search(r'class="article-meta">(.*?)</p>', page, re.S)
    read = re.search(r"(\d+)\s*min read", meta.group(1)) if meta else None
    stated = f"{read.group(1)} min" if read else "—"
    grade = fk_grade(body)
    g = f"{grade:.1f}" if grade is not None else "—"
    intro = intro_paragraphs(page)
    shape = f"intro {len(intro)}¶ ({'/'.join(map(str, intro))})" if intro else ""
    return f"  {path.name:44} {words:6,} words   byline {stated:>7}   computed {max(1, round(words / READ_WPM)):>2} min   FK grade {g}   {shape}"


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
    report = "--report" in argv
    root = Path(argv[argv.index("--path") + 1]) if "--path" in argv else Path(".")
    only = argv[argv.index("--page") + 1] if "--page" in argv else None
    base = None
    if "--baseline" in argv:
        i = argv.index("--baseline")
        base = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "origin/main"

    all_paths = sorted(p for p in root.glob("*.html") if p.name not in SKIP)
    paths = all_paths
    if only:
        paths = [p for p in all_paths if p.name == Path(only).name]
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

    # Listing agreement is measured over the whole site even under --page,
    # since a single article's rows live on three other pages.
    articles = {}
    for p in all_paths:
        facts = article_facts(p, p.read_text(encoding="utf-8"))
        if facts:
            articles[facts["slug"]] = facts
    le, lw = check_listings(root, articles)
    for n, msgs in le.items():
        if not only or n == Path(only).name:
            bad.setdefault(n, []).extend(msgs)
    for n, msgs in lw.items():
        if not only or n == Path(only).name:
            warned.setdefault(n, []).extend(msgs)

    print(f"Prose check — {len(paths)} pages scanned, {len(articles)} articles cross-checked against "
          f"{', '.join(sorted(LISTING))} "
          f"(sentence {SENTENCE_MAX}+, block {PARAGRAPH_MAX}+, title {TITLE_MAX}, description {DESC_MIN}-{DESC_MAX}, "
          f"read time {READ_WPM} wpm ±{READ_TOLERANCE})")
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

    scope = None
    if report:
        scope = {p.name for p in all_paths}
    elif only:
        scope = {Path(only).name}
    elif gated is not None:
        scope = gated
    if scope:
        lines = [readability_line(p, p.read_text(encoding="utf-8"))
                 for p in all_paths if p.name in scope and p.stem in articles]
        if lines:
            print("\nREADABILITY (reported, not gated)")
            print("\n".join(lines))

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
