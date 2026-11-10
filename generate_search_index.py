#!/usr/bin/env python3
"""Generate search-index.json, the data behind search.html.

One entry per thing a reader can land on:

  page     every .html page: title, dek, eyebrow, date, section headings and the full
           text of <main>, so a search reads the body and not only the headline
  row      every row on the Congress Tracker and the State Law Tracker (a bill or a
           statute), linked to its own anchor
  update   every dated entry in a page's Update log (<ul class="dev-list">)
  mdl      every docket on the Mass Tort Map, by MDL number, name, court and judge

Each entry: {k: kind, u: url, t: title, d: dek or one-line read, e: eyebrow or parent
page, w: date (YYYY-MM-DD), h: headings, x: body text}. Empty fields are left out.

search.html fetches the file and matches in the browser, so there is no server side.
The file is rewritten only when its contents change.

    python3 scripts/generate_search_index.py            # write search-index.json
    python3 scripts/generate_search_index.py --check    # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "search-index.json"
SKIP = {"404.html", "search.html"}
LISTING = {"index", "archive", "interviews"}
MAP = "mass-tort-map.html"
BODY_MAX = 60000


def text_of(fragment: str) -> str:
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def first(pattern: str, page: str) -> str:
    m = re.search(pattern, page, re.S)
    return text_of(m.group(1)) if m else ""


def page_entry(name: str, page: str) -> dict:
    slug = name[:-5]
    main = re.search(r"<main\b[^>]*>(.*)</main>", page, re.S)
    main = main.group(1) if main else page
    # The "More from" block and the letter signup repeat on many pages and would match every search.
    main = re.sub(r'<section class="section">.*?</section>', " ", main, flags=re.S) if slug not in LISTING else main
    main = re.sub(r'<aside class="li-letter".*?</aside>', " ", main, flags=re.S)
    main = re.sub(r'<p class="li-letter-top">.*?</p>', " ", main, flags=re.S)
    title = first(r"<h1[^>]*>(.*?)</h1>", main) or first(r"<title>(.*?)</title>", page).split(" | ")[0]
    dek = first(r'<p class="article-dek">(.*?)</p>', main)
    if not dek:
        m = re.search(r'<meta name="description" content="([^"]*)"', page)
        dek = html.unescape(m.group(1)) if m else ""
    m = re.search(r'"dateModified":\s*"(\d{4}-\d{2}-\d{2})', page) or re.search(r'"datePublished":\s*"(\d{4}-\d{2}-\d{2})', page)
    heads = [text_of(h) for h in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", main, re.S)]
    e = {"k": "page", "u": "/" if slug == "index" else slug, "t": title, "d": dek,
         "e": first(r'<p class="article-eyebrow">(.*?)</p>', main), "w": m.group(1) if m else "",
         "h": " · ".join(h for h in heads if h)[:2000],
         # Listing pages repeat every article's title and dek, so their body would match every search.
         "x": "" if slug in LISTING else text_of(main)[:BODY_MAX]}
    return {k: v for k, v in e.items() if v}


def row_entries(name: str, page: str, parent: str) -> list:
    out = []
    for chunk in page.split('<div class="ct-row" id="')[1:]:
        rid, _, body = chunk.partition('">')
        e = {"k": "row", "u": f"{name[:-5]}#{rid}", "t": first(r'<h4 class="ct-name">(.*?)</h4>', body),
             "d": first(r'<p class="ct-line">(.*?)</p>', body), "e": parent,
             "x": " ".join(x for x in (first(r'<span class="ct-num">(.*?)</span>', body),
                                       first(r'<span class="ct-pill[^"]*">(.*?)</span>', body),
                                       first(r'<p class="ct-official">(.*?)</p>', body),
                                       first(r'<dl class="ct-facts">(.*?)</dl>', body)) if x)}
        if e["t"]:
            out.append({k: v for k, v in e.items() if v})
    return out


def update_entries(name: str, page: str, parent: str) -> list:
    m = re.search(r'<ul[^>]*class="[^"]*\bdev-list\b[^"]*"[^>]*>(.*?)</ul>', page, re.S)
    out = []
    for attrs, body in re.findall(r"<li([^>]*)>(.*?)</li>", m.group(1), re.S) if m else []:
        idm = re.search(r'id="(dev-(\d{4}-\d{2}-\d{2})-[a-z0-9-]+)"', attrs)
        lead = re.search(r"<strong>(.*?)</strong>", body, re.S)
        if idm and lead:
            out.append({"k": "update", "u": f"{name[:-5]}#{idm.group(1)}", "t": text_of(lead.group(1)),
                        "d": text_of(body.split("</strong>", 1)[1])[:400], "e": parent, "w": idm.group(2)})
    return out


def mdl_entries(page: str) -> list:
    m = re.search(r'<script type="application/json" id="li-data">(.*?)</script>', page, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    seats = data.get("seats", {})
    out = []
    for d in data.get("dockets", []):
        if not isinstance(d.get("n"), int):
            continue
        seat = seats.get(d.get("d", ""), {})
        pending = d.get("p")
        bits = [seat.get("abbr", ""), f"Judge {d['j']}" if d.get("j") else "",
                f"{pending:,} pending as of the {data.get('report', 'latest')} report" if isinstance(pending, int) else ""]
        out.append({"k": "mdl", "u": MAP[:-5], "t": f"MDL {d['n']}: {d.get('t') or d.get('l') or ''}".rstrip(": "),
                    "d": " · ".join(b for b in bits if b), "e": "Mass Tort Map",
                    "x": " ".join(str(x) for x in (d.get("l"), seat.get("city"), f"MDL No. {d['n']}", f"MDL-{d['n']}") if x)})
    return out


def build() -> list:
    entries = []
    for f in sorted(ROOT.glob("*.html")):
        if f.name in SKIP:
            continue
        page = f.read_text(encoding="utf-8")
        if re.search(r'<meta name="robots" content="[^"]*noindex', page):
            continue
        e = page_entry(f.name, page)
        entries.append(e)
        parent = e.get("t", f.name[:-5])
        entries += row_entries(f.name, page, parent)
        entries += update_entries(f.name, page, parent)
        if f.name == MAP:
            entries += mdl_entries(page)
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 when search-index.json is out of date")
    args = ap.parse_args()
    entries = build()
    body = json.dumps(entries, ensure_ascii=False, separators=(",", ":")) + "\n"
    kinds = {}
    for e in entries:
        kinds[e["k"]] = kinds.get(e["k"], 0) + 1
    print(f"{len(entries)} entries ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))}), {len(body) // 1024} KB")
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if old == body:
        print(f"{OUT.name} already up to date")
        return 0
    if args.check:
        print(f"{OUT.name} is out of date")
        return 1
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
