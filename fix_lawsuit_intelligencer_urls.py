from pathlib import Path
import re

# Start with True.
# After the dry run looks right, change this to False and run again.
DRY_RUN = False

DOMAIN = "https://lawsuitintelligencer.com"

# The script scans the folder where this script is saved.
ROOT = Path(__file__).resolve().parent

PROCESS_EXTENSIONS = {".html", ".xml", ".json"}

IGNORE_FILES = {"_redirects", "netlify.toml"}

IGNORE_FOLDERS = {".git", "node_modules", ".netlify", "__pycache__"}

# Special case:
# https://lawsuitintelligencer.com/index.html
# becomes:
# https://lawsuitintelligencer.com/
index_pattern = re.compile(
    re.escape(DOMAIN) + r"/index\.html(?=([#?])|[\"'\s<>\)\]]|$)"
)

# General case:
# https://lawsuitintelligencer.com/about.html
# becomes:
# https://lawsuitintelligencer.com/about
html_pattern = re.compile(
    r"("
    + re.escape(DOMAIN)
    + r"/(?!index\.html\b)[^\"'\s<>\)\]\?#]+?)"
    r"\.html"
    r"(?=([#?])|[\"'\s<>\)\]]|$)"
)


def should_skip(path):
    if path.name in IGNORE_FILES:
        return True

    if any(folder in path.parts for folder in IGNORE_FOLDERS):
        return True

    if path.suffix.lower() not in PROCESS_EXTENSIONS:
        return True

    return False


def fix_content(text):
    count = 0

    def fix_index(match):
        nonlocal count
        count += 1
        return DOMAIN + "/"

    text = index_pattern.sub(fix_index, text)

    def fix_html(match):
        nonlocal count
        count += 1
        return match.group(1)

    text = html_pattern.sub(fix_html, text)

    return text, count


print("ROOT:", ROOT)
print("DOMAIN:", DOMAIN)
print("MODE:", "DRY RUN - no files will be changed" if DRY_RUN else "WRITE MODE - files will be changed")
print()

changed_files = 0
total_changes = 0

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue

    if should_skip(path):
        continue

    try:
        old_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print("Skipped non-UTF-8 file:", path.name)
        continue

    new_text, count = fix_content(old_text)

    if count == 0 or new_text == old_text:
        continue

    changed_files += 1
    total_changes += count

    print(f"{path.relative_to(ROOT)}: {count} change(s)")

    if not DRY_RUN:
        path.write_text(new_text, encoding="utf-8")

print()
print("Changed files:", changed_files)
print("Total changes:", total_changes)

if DRY_RUN:
    print()
    print("Dry run only. Nothing was changed.")
    print("After checking the output, change DRY_RUN = True to DRY_RUN = False and run again.")
else:
    print()
    print("Done. Files were changed.")
