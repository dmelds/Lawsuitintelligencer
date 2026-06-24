#!/usr/bin/env python3
"""
Generate sitemap.xml for Lawsuit Intelligencer.

Scans the repository root for top-level .html files, maps each to its
extensionless public URL, and writes sitemap.xml. lastmod is taken from the
file's last git commit date so it stays honest without manual editing.

No third-party dependencies. Runs on the stock Python 3 available on the
GitHub Actions ubuntu-latest runner, and locally.

    python3 scripts/generate_sitemap.py
"""

from __future__ import annotations

import datetime as _dt
import subprocess
import sys
from pathlib import Path

BASE_URL = "https://lawsuitintelligencer.com"

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


def git_lastmod(path: Path) -> str:
    """Last commit date (YYYY-MM-DD) for a file, or today if unavailable."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", path.name],
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


def build() -> str:
    pages = sorted(
        (p for p in ROOT.glob("*.html") if p.name not in EXCLUDE),
        key=sort_key,
    )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "",
    ]
    for p in pages:
        priority, changefreq = settings_for(p)
        lines += [
            "  <url>",
            f"    <loc>{url_for(p)}</loc>",
            f"    <lastmod>{git_lastmod(p)}</lastmod>",
            f"    <changefreq>{changefreq}</changefreq>",
            f"    <priority>{priority}</priority>",
            "  </url>",
            "",
        ]
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def main() -> int:
    xml = build()
    out = ROOT / "sitemap.xml"
    out.write_text(xml, encoding="utf-8")
    count = xml.count("<url>")
    print(f"Wrote {out.relative_to(ROOT)} with {count} URLs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
