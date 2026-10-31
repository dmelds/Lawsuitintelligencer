#!/usr/bin/env python3
"""Put the publication's mailing address and editorial email on every page.

Lawsuit Intelligencer lists where the publication can be reached, the way a
publisher's masthead does. The line carries the office address and the
editor@ address. It carries no advertising label and no intake number,
because the footer disclosure states the publication is independent of any
case, claim, or law firm it covers.

Two edits:

  1. Footer. The address and email go on a second line inside the existing
     footer-bottom paragraph, after "Edited by David Meldofsky", joined with
     a <br>. The paragraph keeps its class, so the line inherits the mono
     11px style and needs no CSS.
  2. Organization schema. Every JSON-LD block that defines
     https://lawsuitintelligencer.com/#organization gets a PostalAddress and
     the email, inserted after its "url" line. Blocks that already carry an
     "address" are left alone.

A page that already carries the contact line is left alone, so this is safe
to rerun and safe to schedule. If the address or email changes, edit ADDRESS
and EMAIL and rerun; the old line is swapped for the new one on every page.

A page whose footer-bottom paragraph does not match is reported as a problem
and left untouched rather than guessed at. The JSON-LD on every changed page
is parsed after the edit, and a page that no longer parses is reported and
not written.

Usage
-----
    python3 scripts/add_masthead_contact.py
    python3 scripts/add_masthead_contact.py --apply

Options
-------
    --path DIR   directory to scan (default .)
    --apply      write changes; without it the script only reports
"""

import argparse
import json
import os
import re

STREET = "6210 Wilshire Blvd. Ste 200"
CITY = "Los Angeles"
REGION = "CA"
POSTAL = "90048"
EMAIL = "editor@lawsuitintelligencer.com"
ADDRESS = f"{STREET}, {CITY}, {REGION} {POSTAL}"

CONTACT = (
    '<br><span class="footer-contact">' + ADDRESS + ' &middot; '
    '<a href="mailto:' + EMAIL + '">' + EMAIL + '</a></span>'
)
CONTACT_RE = re.compile(r'<br><span class="footer-contact">.*?</span>', re.S)
EDITED = 'Edited by <a href="david-meldofsky">David Meldofsky</a>'
BOTTOM_RE = re.compile(r'<p class="footer-bottom">(.*?)</p>', re.S)

ORG_RE = re.compile(
    r'(?P<indent>[ \t]*)"@id": "https://lawsuitintelligencer\.com/#organization",\n'
    r'[ \t]*"name": "Lawsuit Intelligencer",\n'
    r'[ \t]*"url": "https://lawsuitintelligencer\.com/",\n'
)
LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def org_lines(indent):
    i, j = indent, indent + "  "
    return (
        f'{i}"address": {{\n'
        f'{j}"@type": "PostalAddress",\n'
        f'{j}"streetAddress": "{STREET}",\n'
        f'{j}"addressLocality": "{CITY}",\n'
        f'{j}"addressRegion": "{REGION}",\n'
        f'{j}"postalCode": "{POSTAL}",\n'
        f'{j}"addressCountry": "US"\n'
        f'{i}}},\n'
        f'{i}"email": "{EMAIL}",\n'
    )


def sweep_footer(html):
    m = list(BOTTOM_RE.finditer(html))
    if len(m) != 1:
        return html, [], ["footer-bottom found %d times" % len(m)]
    m = m[0]
    inner = m.group(1)
    if "footer-contact" in inner:
        new_inner = CONTACT_RE.sub(CONTACT, inner)
        if new_inner == inner:
            return html, [], []
        note = "contact line updated"
    elif inner.rstrip().endswith(EDITED):
        new_inner = inner.rstrip() + CONTACT
        note = "contact line"
    else:
        return html, [], ["footer-bottom does not end with the Edited by line"]
    start, end = m.span(1)
    return html[:start] + new_inner + html[end:], [note], []


def sweep_schema(html):
    out, pos, n = [], 0, 0
    for m in ORG_RE.finditer(html):
        # Skip a JSON-LD block that already carries an address.
        block_start = html.rfind("<script", 0, m.start())
        block_end = html.find("</script>", m.end())
        if '"address"' in html[block_start:block_end]:
            continue
        out.append(html[pos:m.end()])
        out.append(org_lines(m.group("indent")))
        pos = m.end()
        n += 1
    out.append(html[pos:])
    return "".join(out), (["schema address x%d" % n] if n else [])


def parses(html):
    try:
        for block in LD_RE.findall(html):
            json.loads(block)
        return True
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=".")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    changed, current, problems = [], [], []
    for root, dirs, files in os.walk(args.path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for fn in sorted(files):
            if not fn.endswith(".html"):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, args.path)
            with open(p, encoding="utf-8") as fh:
                html = fh.read()
            if "site-footer" not in html:
                continue
            new, notes, probs = sweep_footer(html)
            if probs:
                problems.append((rel, probs))
                continue
            new, snotes = sweep_schema(new)
            notes += snotes
            if new == html:
                current.append(rel)
                continue
            if not parses(new):
                problems.append((rel, ["JSON-LD no longer parses after edit"]))
                continue
            changed.append((rel, notes))
            if args.apply:
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(new)

    verb = "changed" if args.apply else "would change"
    print(f"{len(changed):3d} pages {verb}")
    print(f"{len(current):3d} pages already current")
    print(f"{len(problems):3d} pages with problems")
    print()
    for rel, notes in changed:
        print(f"  {rel}: {', '.join(notes)}")
    if problems:
        print()
        for rel, probs in problems:
            print(f"  PROBLEM {rel}: {', '.join(probs)}")
        print()
        print("Problem pages were left untouched. Fix them, then rerun.")


if __name__ == "__main__":
    main()
