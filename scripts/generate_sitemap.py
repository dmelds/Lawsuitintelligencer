#!/usr/bin/env python3
"""
Generate sitemap.xml for Lawsuit Intelligencer.

Scans the repository root for top-level .html files, maps each to its
extensionless public URL, and writes sitemap.xml.

lastmod is taken from the page's own JSON-LD dateModified, falling back to
datePublished, because that date is editorial truth: it is what the byline
block and the structured data already tell readers and crawlers. Deriving it
from git instead made <lastmod> a commit artifact, so any mechanical sweep
across many files -- a nav rebuild, a related-links refresh, a tracking
snippet -- restamped every page it touched and flattened the whole sitemap to
a single day. Pages carrying no date in their schema (index, about,
contribute, editorial-standards) still fall back to the git commit date, with
SKIP_TOKEN honored as before.

No third-party dependencies. Runs on the stock Python 3 available on the
GitHub Actions ubuntu-latest runner, and locally.

    python3 scripts/generate_sitemap.py
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

BASE_URL = "https://lawsuitintelligencer.com"

# Commits whose message contains this token are ignored when computing a
# git-derived <lastmod>. Only pages with no date in their JSON-LD reach that
# path now, so this is a backstop rather than the main defense.
SKIP_TOKEN = "[skip lastmod]"

# Files that exist on disk but must never appear in the sitemap.
EXCLUDE = {"404.html"}

# Per-page priority and change frequency. Anything not listed here uses
# DEFAULT. index.html is always treated as the homepage.
HOME = ("1.0", "weekly")
DEFAULT = ("0.8", "monthly")
OVERRIDES = {
    "welcome": ("0.9", "monthly"),
    "about": ("0.7", "monthly"),
    "editorial-standards": ("0.7", "monthly"),
    "contribute": ("0.6", "monthly"),
    "david-meldofsky": ("0.7", "monthly"),
    "mass-tort-map-2026": ("0.9", "monthly"),
    "mso-indirect-fee-sharing": ("0.9", "monthly"),
    "xai-data-center-class-action": ("0.9", "weekly"),
    "ai-wrongful-death-docket": ("0.9", "weekly"),
    "fable-5-section-230-defense": ("0.9", "weekly"),
    "legion-fable-5-lawsuit": ("0.9", "weekly"),
}

ROOT = Path(__file__).resolve().parent.parent

LD_BLOCK = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)

# Preference order within a page's structured data.
DATE_KEYS = ("dateModified", "datePublished")


def _iter_nodes(obj):
    """Walk every dict in a JSON-LD document, including @graph and arrays."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_nodes(item)


def _as_date(value) -> str | None:
    """Normalize an ISO date or datetime to YYYY-MM-DD, or None if unusable.

    Accepts '2026-08-15' and '2026-08-15T06:00:00-07:00' alike. The calendar
    date is taken as written, with no timezone conversion: the date the byline
    shows is the date the sitemap should claim.
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    try:
        _dt.date.fromisoformat(head)
    except ValueError:
        return None
    return head


def schema_lastmod(path: Path) -> str | None:
    """Best date from the page's JSON-LD, or None if it has none.

    A malformed or absent block is not an error; the caller falls back to git.
    """
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    found: dict[str, str] = {}
    for raw in LD_BLOCK.findall(html):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for node in _iter_nodes(data):
            for key in DATE_KEYS:
                if key in found:
                    continue
                date = _as_date(node.get(key))
                if date:
                    found[key] = date

    for key in DATE_KEYS:
        if key in found:
            return found[key]
    return None


def git_lastmod(path: Path) -> str:
    """Last commit date (YYYY-MM-DD) for a file, or today if unavailable.

    Commits whose message contains SKIP_TOKEN are ignored. Falls back to the
    unfiltered date for files whose only commits are marked.
    """
    try:
        for extra in (["-F", f"--grep={SKIP_TOKEN}", "--invert-grep"], []):
            out = subprocess.run(
                ["git", "log", "-1", "--format=%cs", *extra, "--", path.name],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if out:
                return out
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # Fallbacks: filesystem mtime, then today.
    try:
        return _dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return _dt.date.today().isoformat()


def lastmod_for(path: Path) -> tuple[str, str]:
    """Return (date, source), where source is 'schema' or 'git'."""
    date = schema_lastmod(path)
    if date:
        return date, "schema"
    return git_lastmod(path), "git"


def url_for(path: Path) -> str:
    if path.name == "index.html":
        return f"{BASE_URL}/"
    return f"{BASE_URL}/{path.stem}"


def settings_for(path: Path) -> tuple[str, str]:
    if path.name == "index.html":
        return HOME
    return OVERRIDES.get(path.stem, DEFAULT)


def sort_key(path: Path):
    # Home first, then highest priority first, then alphabetical for stable diffs.
    priority, _ = settings_for(path)
    return (path.name != "index.html", -float(priority), path.stem)


def build() -> tuple[str, list[str]]:
    pages = sorted(
        (p for p in ROOT.glob("*.html") if p.name not in EXCLUDE),
        key=sort_key,
    )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "",
    ]
    from_git: list[str] = []
    for p in pages:
        priority, changefreq = settings_for(p)
        lastmod, source = lastmod_for(p)
        if source == "git":
            from_git.append(p.stem)
        lines += [
            "  <url>",
            f"    <loc>{url_for(p)}</loc>",
            f"    <lastmod>{lastmod}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
            "",
        ]
    lines.append("</urlset>")
    return "\n".join(lines) + "\n", from_git


def main() -> int:
    xml, from_git = build()
    out = ROOT / "sitemap.xml"
    out.write_text(xml, encoding="utf-8")
    count = xml.count("<url>")
    print(f"Wrote {out.relative_to(ROOT)} with {count} URLs.")
    print(f"lastmod from schema: {count - len(from_git)}, from git: {len(from_git)}")
    if from_git:
        print("  git-dated pages: " + ", ".join(sorted(from_git)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
