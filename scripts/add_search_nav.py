#!/usr/bin/env python3
"""Put a Search link in the masthead navigation of every page.

The link goes last in <nav class="masthead-nav">, after Contribute. A page that already
carries it is left alone, so this is safe to rerun. A page with no masthead nav is
reported and left untouched. New pages copy their shell from an existing page, so they
arrive with the link.

    python3 scripts/add_search_nav.py            # report only
    python3 scripts/add_search_nav.py --apply    # write changes
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = '<a href="search">Search</a>'
NAV = re.compile(r'(<nav class="masthead-nav"[^>]*>)(.*?)(\n?[ \t]*</nav>)', re.S)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes; without it the script only reports")
    args = ap.parse_args()
    changed, problems = [], []
    for f in sorted(ROOT.glob("*.html")):
        page = f.read_text(encoding="utf-8")
        m = NAV.search(page)
        if not m:
            problems.append(f.name)
            continue
        if 'href="search"' in m.group(2):
            continue
        links = re.findall(r"\n([ \t]*)<a ", m.group(2))
        indent = links[-1] if links else "          "
        new = page[:m.start()] + m.group(1) + m.group(2).rstrip() + "\n" + indent + LINK + m.group(3) + page[m.end():]
        changed.append(f.name)
        if args.apply:
            f.write_text(new, encoding="utf-8")
    verb = "changed" if args.apply else "would change"
    print(f"  {len(changed)} pages {verb}" + (": " + ", ".join(changed) if changed else ""))
    print(f"  {len(problems)} pages with problems" + (": " + ", ".join(problems) if problems else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
