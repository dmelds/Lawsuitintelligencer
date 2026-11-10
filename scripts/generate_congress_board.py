#!/usr/bin/env python3
"""Generate congress-tracker.html, the Congress Tracker, from Hill Wire.

The trade-side sibling of Lawsuit Informer's Congress board
(mass-tort-bills-in-congress). Same source, written for practitioners: every bill
Hill Wire watches, the litigation funding and qui tam bills included, grouped by
beat, with its stage, its last move, its latest action as Congress.gov prints it,
the key-move history, and a line on what the bill would do to the litigation.

Two inputs:

  data/congress_bills.json   Hill Wire's watch list, saved from its /api/state.
                             `--fetch` refreshes it when HILL_KEY is set (the
                             workflow does this). The file is rewritten only when
                             a bill's stage, actions or text hits changed, so a
                             quiet poll commits nothing. Without the key, or when
                             Hill Wire cannot be reached, the saved file is used.
  data/congress_lines.txt    The words, kept by hand:
                               [cases]   Hill Wire topic|beat name
                               [bills]   bill id|name|beat|what the bill would do
                                         (empty name = Hill Wire's label; empty beat
                                         = the topic's beat, which a topic "-" bill
                                         must supply)
                               [riders]  phrase|why that phrase matters
                             A bill shows only once it has a [bills] line. Hill
                             Wire's own notes are desk notes and never reach the page.

What a row shows, all from the saved state:
  stage       Hill Wire's stage (Introduced, In committee, Reported, Passed House,
              Passed Senate, Passed both, To President, Law; amendments run Submitted
              to Agreed to)
  last move   the newest action that changes where the bill stands, by the rule
              network-ops scripts/hill_review.py uses: passage, reported, calendar,
              cloture, presented, signed, an amendment adopted or rejected.
              Referrals and floor procedure never count.
  latest      Congress.gov's latest action of any kind, record cites trimmed
  history     every key move Hill Wire holds for the bill, behind a <details>

Latest moves is the page's Update log, so the monthly letter (network-ops
scripts/intelligencer_letter.py) reads it the way it reads the map and the other
living pages: a <ul class="dev-list" data-page="congress-tracker">, one <li> per
bill per day, id dev-YYYY-MM-DD-<bill id>, data-kind "congress", and a bold lead
sentence naming who acted, on which bill, and the vote when there is one. The
text after the lead is the bill's one-line read from congress_lines.txt, which
the letter prints as the detail. When one bill has several moves on one day,
the entry carries the furthest one (passage over a failed motion, a committee
report over calendar placement). Keep the id form stable: the letter tells new
moves from old ones by id.

Bills from a Congress that has ended show as expired. The 119th ends January 3,
2027; bills that have not been enacted by then die and return, if at all, with
new numbers.

The page shell (head, masthead, footer) is the existing page on disk. On the
first run, when the page does not exist yet, the shell is copied from
state-ai-enforcement-tracker.html with the URL, title, descriptions, JSON-LD and
page styles swapped. After that only <main>, the <title>, the descriptions and
the JSON-LD dates are rewritten. The masthead issue line is check_issue_line.py's
(this page is in its EVERGREEN set) and rolls with roll_issue_line.py.

The page is written only when the rows changed, and then dateModified and the
"Last updated" line move to today.

    python3 scripts/generate_congress_board.py --fetch     # refresh, then build
    python3 scripts/generate_congress_board.py             # build from saved data
    python3 scripts/generate_congress_board.py --dry-run   # print rows, write nothing
    python3 scripts/generate_congress_board.py --force     # write even if unchanged
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://lawsuitintelligencer.com"
SLUG = "congress-tracker"
OUT = ROOT / f"{SLUG}.html"
SHELL_FROM = ROOT / "state-ai-enforcement-tracker.html"
DATA = ROOT / "data" / "congress_bills.json"
LINES = ROOT / "data" / "congress_lines.txt"
HILL_URL = (os.environ.get("HILL_URL") or "https://hill.masstortwire.com").rstrip("/")
UA = "lawsuitintelligencer congress tracker (+https://lawsuitintelligencer.com)"
TITLE = "Congress Tracker: Mass Tort and AI Liability Bills"
OG_TITLE = "Congress Tracker: Every Federal Bill on Mass Torts, AI Liability and Litigation Funding"
H1 = "Congress Tracker"
RECENT_DAYS = 30
LATEST_MOVES = 10
MOVES_KEPT = 15
MOVES_SINCE = 180
SNIPPET = 320

CONGRESS_ENDS = {119: "2027-01-03", 120: "2029-01-03"}

# Same expressions as network-ops scripts/hill_review.py (and Hill Wire's page for
# PROCEDURAL) and Informer's generate_congress_board.py. Change all of them together.
PROCEDURAL = re.compile(
    r"^(debate|postponed proceedings|considered (as unfinished business|under suspension|by unanimous consent)|"
    r"the house (rose|resolved|adopted the amendments en gros)|committee of the whole|the previous question|"
    r"motion to reconsider laid on the table|the clerk was authorized|at the conclusion of debate|"
    r"on motion that the committee rise|pursuant to the provisions)|"
    r"moved (to recommit|that the committee rise|to suspend the rules)|rises leaving|"
    r"motion to proceed to consideration of measure (made|withdrawn)|"
    r"cloture motion on the motion to proceed .*presented|motion by senator .* to reconsider the vote|"
    r"^mr\.|^ms\.|^mrs\.", re.I)
KEY_MOVE = re.compile(
    r"became public law|signed by (the )?president|presented to (the )?president|vetoed|"
    r"\bpassed\b|\bfailed\b|agreed to|withdrawn|\breported\b|placed on .*calendar|"
    r"cloture .*(invoked|not invoked)|conference report|resolving differences", re.I)
NOT_A_MOVE = re.compile(
    r"^referred to|^introduced in|^read twice and referred|^received in the|supplemental report|"
    r"motion to reconsider|motion to proceed to consideration of measure|mark-?up session held", re.I)

TYPE_LABEL = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.",
              "hconres": "H.Con.Res.", "sconres": "S.Con.Res.", "hres": "H.Res.",
              "sres": "S.Res.", "samdt": "S.Amdt.", "hamdt": "H.Amdt.", "suamdt": "S.Up.Amdt."}

# Hill Wire stage -> (label, step on the six-step track, tone)
STAGES = {
    "Introduced": ("Introduced", 1, "quiet"),
    "In committee": ("In committee", 2, "quiet"),
    "Reported": ("Reported", 3, "watch"),
    "Passed House": ("Passed House", 4, "watch"),
    "Passed Senate": ("Passed Senate", 4, "watch"),
    "Passed both": ("Passed both chambers", 5, "live"),
    "To President": ("Presented to President", 5, "live"),
    "Law": ("Enacted", 6, "live"),
}
AMDT_STAGES = {
    "Filed": ("Submitted", 0, "quiet"),
    "Submitted": ("Submitted", 0, "quiet"),
    "Proposed": ("Proposed", 0, "watch"),
    "Agreed to": ("Agreed to", 0, "live"),
    "Not agreed to": ("Not agreed to", 0, "quiet"),
    "Withdrawn": ("Withdrawn", 0, "quiet"),
}
TRACK = ["Introduced", "Committee", "Reported", "One chamber", "Both chambers", "Enacted"]
CITE = re.compile(r"\s*\((?:[^()]*\bCR\b[^()]*|Roll no\.[^()]*|text:[^()]*|consideration:[^()]*)\)")


def warn(msg: str) -> None:
    print(f"::warning::{msg}")


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def is_amdt(t: str) -> bool:
    return (t or "").endswith("amdt")


def id_text(b: dict) -> str:
    return f"{TYPE_LABEL.get(b.get('type', ''), (b.get('type') or '').upper())} {b.get('number', '')}".strip()


def to_date(s):
    try:
        return _dt.date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def short(iso: str) -> str:
    d = _dt.date.fromisoformat(iso)
    return d.strftime("%b").upper() + " " + str(d.day) + ", " + str(d.year)


def long_(iso: str) -> str:
    d = _dt.date.fromisoformat(iso)
    return d.strftime("%B") + f" {d.day}, {d.year}"


REFERRAL_TAIL = re.compile(r",? for a period to be subsequently determined by the Speaker, in each case for consideration "
                          r"of such provisions as fall within the jurisdiction of the committee concerned", re.I)


def clean(text: str) -> str:
    """An action as Congress.gov prints it, with record cites, the House referral boilerplate and
    the "Passed/agreed to in <chamber>:" summary prefix trimmed."""
    t = CITE.sub("", (text or "").strip())
    t = REFERRAL_TAIL.sub("", t)
    t = re.sub(r"^Passed/agreed to in (House|Senate):\s*", "", t)
    t = re.sub(r"\s*Record Vote Number:\s*\d+\.?", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if t.endswith(".") else t + "."


# ---------------------------------------------------------------- the Update log (Latest moves)

VOTE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,3})")
COMMITTEE = re.compile(r"Committee on ((?:the )?[A-Z][A-Za-z,' ]*?[A-Za-z])(?=\.|\s+H\. Rept|$|\s+Original|\s+Reported)")

# The furthest step wins when one bill has several moves on one day.
MOVE_RANK = [
    (r"became public law|signed by (the )?president", 10),
    (r"vetoed", 9),
    (r"presented to (the )?president", 8),
    (r"conference report|resolving differences", 7),
    (r"\bpassed\b|suspend the rules and pass|agreed to", 6),
    (r"cloture", 5),
    (r"\breported\b(?! to house)", 4),
    (r"rules committee resolution", 3),
    (r"placed on .*calendar", 2),
]


def move_rank(text: str) -> int:
    low = (text or "").lower()
    if "motion to recommit" in low:
        return 1
    for pat, n in MOVE_RANK:
        if re.search(pat, low):
            return n
    return 0


def bare_name(name: str) -> str:
    """The bill's name for a sentence: the "(House)"/"(Senate)" companion tag dropped, since the
    number already tells the two apart, and "the" in front unless the name carries its own article."""
    n = re.sub(r"\s*\((?:House|Senate)\)\s*$", "", name or "").strip()
    return n if re.match(r"(?i)(the|a|an)\s", n) else "the " + n


def tally(text: str) -> str:
    low = text.lower()
    v = VOTE.search(text)
    if v:
        return f", {v.group(1)} to {v.group(2)}"
    if "unanimous consent" in low:
        return " by unanimous consent"
    if "voice vote" in low:
        return " by voice vote"
    return ""


def committee_of(text: str, others: list) -> str:
    """The committee an action names, else the one committee the bill's other moves name."""
    m = COMMITTEE.search(text or "")
    if m:
        return m.group(1)
    found = {c.group(1) for t in others for c in [COMMITTEE.search(t or "")] if c}
    return found.pop() if len(found) == 1 else ""


def lead_sentence(r: dict, text: str) -> str:
    """One Congress.gov action as a trade-press lead: who acted, on which bill, the vote."""
    low = text.lower()
    bill = f'{r["bill"]}, {bare_name(r["name"])}'
    house_bill = r["type"].startswith("h")
    chamber = "House" if house_bill else "Senate"
    votes = tally(text)
    if re.search(r"became public law|signed by (the )?president", low):
        return f"The President signed {bill}, into law."
    if "vetoed" in low:
        return f"The President vetoed {bill}."
    if re.search(r"presented to (the )?president", low):
        return f"Congress presented {bill}, to the President."
    if re.search(r"conference report|resolving differences", low):
        return f"House and Senate negotiators reported a conference version of {bill}."
    if r["amdt"]:
        if "not agreed to" in low:
            return f"The {chamber} rejected {bill}{votes}."
        if "withdrawn" in low:
            return f"The sponsor withdrew {bill}."
        if "agreed to" in low:
            return f"The {chamber} adopted {bill}{votes}."
    if "cloture" in low:
        target = "the motion to proceed to " if "motion to proceed" in low else ""
        verb = "failed to invoke" if "not invoked" in low else "invoked"
        return f"The Senate {verb} cloture on {target}{bill}{votes}."
    if "motion to recommit" in low:
        verb = "rejected" if "failed" in low else "adopted"
        return f"The House {verb} a motion to recommit {bill}{votes}."
    if re.search(r"suspend the rules and pass", low):
        return f"The House passed {bill}, under suspension of the rules{votes}."
    amended = ", as amended" if re.search(r"with an amendment|with amendments|as amended", low) else ""
    if re.search(r"on passage passed|passed (the )?house|passed/agreed to in house", low):
        return f"The House passed {bill}{amended}{votes}."
    if re.search(r"passed (the )?senate|passed/agreed to in senate", low):
        return f"The Senate passed {bill}{amended}{votes}."
    if "rules committee resolution" in low:
        rule = re.search(r"H\. ?Res\. ?\d+", text)
        what = f"{rule.group(0)}, a rule for floor debate on" if rule else "a rule for floor debate on"
        return f"The House Rules Committee reported {what} {bill}."
    if re.search(r"placed on senate legislative calendar", low):
        return f"The Senate placed {bill}, on its legislative calendar."
    if re.search(r"placed on the (union|house) calendar", low):
        which = "Union" if "union" in low else "House"
        return f"The House placed {bill}, on the {which} Calendar."
    if re.search(r"reported|ordered to be reported", low):
        # Senate committee actions open "Committee on X."; House ones say "by the Committee on X".
        c_chamber = "Senate" if re.match(r"committee on|.*reported to senate", low) else chamber
        name = committee_of(text, [t for _, t in r["moves"]])
        who = f"The {c_chamber} Committee on {name}" if name else f"A {c_chamber} committee"
        how = ""
        if "nature of a substitute" in low:
            how = ", with a substitute amendment"
        elif "(amended)" in low or "with an amendment" in low or "with amendments" in low:
            how = ", with amendments"
        if "ordered to be reported" in low:
            return f"{who} voted to report {bill}{how}{votes}."
        if "original measure" in low:
            return f"{who} reported {bill}, as an original bill."
        return f"{who} reported {bill}{how}."
    if "failed" in low:
        return f"A {chamber} vote on {bill}, failed{votes}."
    first = re.split(r"(?<=[a-z])\.\s", text, maxsplit=1)[0].rstrip(".")
    return f"Congress.gov records a new action on {bill}: {first}."


def update_log(rows: list, today: _dt.date) -> list:
    """[(iso, row, lead)] for Latest moves: one per bill per day, the furthest move that day,
    newest first, inside MOVES_SINCE and capped at LATEST_MOVES."""
    floor = today - _dt.timedelta(days=MOVES_SINCE)
    best = {}
    for r in rows:
        for d, t in r["moves"]:
            dd = to_date(d)
            if not dd or not (floor <= dd <= today):
                continue
            key = (d, r["id"])
            if key not in best or move_rank(t) > move_rank(best[key][1]):
                best[key] = (r, t)
    out = [(d, r, lead_sentence(r, t)) for (d, _), (r, t) in best.items()]
    out.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
    return out[:LATEST_MOVES]


NAME_TOKEN = re.compile(r"(?:with )?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve) "
                        r"(?:others|cosponsors)|[A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+){0,2}(?: of [A-Z][a-z]+)?")


def log_detail(line: str) -> str:
    """The bill's one-line read for Latest moves, without the sponsor list many lines open with
    ("Cruz, Schatz, Curtis and Schiff."). The letter prints the first sentence after the lead, and a
    list of names tells a reader nothing about the move. The row below keeps the full line."""
    m = re.match(r"([^.]*)\.\s+(?=[A-Z])", line or "")
    if m:
        parts = [x for x in re.split(r",\s*(?:and\s+)?|\s+and\s+|,?\s+with\s+", m.group(1)) if x]
        if parts and all(NAME_TOKEN.fullmatch(x.strip()) for x in parts):
            return line[m.end():]
    return line


def render_log_item(d: str, r: dict, lead: str) -> str:
    return (f'<li id="dev-{d}-{esc(r["id"])}" data-kind="congress"><time datetime="{d}">{short(d)}</time>'
            f'<span class="ct-beat-tag">{esc(r["beat"])}</span>'
            f'<strong><a href="#{esc(r["id"])}">{esc(lead)}</a></strong> {esc(log_detail(r["line"]))}</li>')


# ---------------------------------------------------------------- the data file


def is_move(text: str) -> bool:
    t = (text or "").strip()
    if not t or PROCEDURAL.search(t) or NOT_A_MOVE.search(t):
        return False
    return bool(KEY_MOVE.search(t))


def moves_of(actions: list) -> list:
    acts = [a for a in actions or [] if is_move(a.get("text"))]
    keep = []
    for a in acts:
        if any(o is not a and o.get("date") == a.get("date") and len(o["text"]) > len(a["text"])
               and a["text"] in o["text"] for o in acts):
            continue
        keep.append({"date": (a.get("date") or "")[:10], "text": a["text"].strip()})
    keep.sort(key=lambda a: a["date"], reverse=True)
    return keep[:MOVES_KEPT]


def text_url(b: dict) -> str:
    """GovInfo's link service for the bill's newest text (same as hill_review.py); none for amendments."""
    t = (b.get("type") or "").lower()
    if not t or is_amdt(t) or not b.get("number") or not b.get("congress"):
        return ""
    return f"https://www.govinfo.gov/link/bills/{b['congress']}/{t}/{b['number']}?link-type=html"


def trim_state(state: dict) -> dict:
    bills = {}
    for key, b in (state.get("bills") or {}).items():
        bid = b.get("id") or key
        latest = b.get("latest") or {}
        ab = b.get("amendedBill")
        bills[bid] = {
            "id": bid, "congress": b.get("congress"), "type": b.get("type"), "number": str(b.get("number") or ""),
            "label": b.get("label") or "", "title": b.get("title") or "", "topic": b.get("topic") or "-",
            "pages": b.get("pages") or [], "url": b.get("url") or "", "text_url": text_url(b),
            "introduced": (b.get("introduced") or "")[:10], "stage": b.get("stage") or "",
            "latest": {"date": (latest.get("date") or "")[:10], "text": (latest.get("text") or "").strip()},
            "moves": moves_of(b.get("actions")),
            "amended_bill": ({"label": id_text(ab), "url": ab.get("url", ""), "title": ab.get("title", "")}
                             if ab else None),
        }
    hits = []
    for h in (state.get("hits") or {}).values():
        bill = h.get("bill") or {}
        hits.append({
            "phrase": h.get("phrase") or "", "topic": h.get("topic") or "-", "pages": h.get("pages") or [],
            "bill": id_text(bill), "bill_id": bill.get("id") or "", "bill_url": bill.get("url") or "",
            "label": h.get("title") or "", "link": h.get("link") or "", "removed": bool(h.get("removed")),
            "version_date": (h.get("versionDate") or "")[:10], "version_type": h.get("versionType") or "",
            "first_seen": (h.get("firstSeen") or "")[:10], "count": h.get("count") or 0,
            "snippet": (h.get("snippet") or "")[:SNIPPET],
        })
    hits.sort(key=lambda x: (x["bill_id"], x["phrase"], x["removed"]))
    return {"source": HILL_URL + "/api/state", "bills": dict(sorted(bills.items())), "hits": hits}


def fetch(dry_run: bool) -> None:
    key = (os.environ.get("HILL_KEY") or "").strip()
    if not key:
        warn("HILL_KEY is not set; building from the saved data/congress_bills.json")
        return
    req = urllib.request.Request(HILL_URL + "/api/state", headers={"X-Wire-Key": key, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            state = (json.loads(r.read().decode("utf-8")) or {}).get("state")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            warn("Hill Wire refused HILL_KEY (401). It must equal the WIRE_KEY on the hill-wire Netlify project. "
                 "Building from the saved data.")
        else:
            warn(f"Hill Wire answered HTTP {e.code}; building from the saved data")
        return
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        warn(f"Hill Wire could not be read ({e}); building from the saved data")
        return
    if not state or not state.get("bills"):
        warn("Hill Wire has no saved state yet; building from the saved data")
        return
    run = state.get("lastRun") or {}
    print(f"Hill Wire last polled {run.get('at', '?')}: {run.get('billCount', '?')} bills, ok={run.get('ok')}")
    new = trim_state(state)
    old = None
    if DATA.exists():
        try:
            old = json.loads(DATA.read_text(encoding="utf-8"))
        except ValueError:
            old = None
    if old == new:
        print(f"{DATA.relative_to(ROOT)} unchanged")
        return
    if dry_run:
        print(f"{DATA.relative_to(ROOT)} would change")
        return
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(new, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {DATA.relative_to(ROOT)}")


# ---------------------------------------------------------------- the words


def load_lines() -> dict:
    out = {"cases": {}, "bills": {}, "riders": {}}
    if not LINES.exists():
        sys.exit(f"{LINES} not found")
    section = None
    for raw in LINES.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(cases|bills|riders)\]$", line, re.I)
        if m:
            section = m.group(1).lower()
            continue
        if not section or "|" not in line:
            continue
        if section == "bills":
            parts = [p.strip() for p in line.split("|", 3)]
            if len(parts) == 4 and parts[0] and parts[3]:
                out["bills"][parts[0]] = (parts[1], parts[2], parts[3])
            else:
                warn(f"{LINES.name}: [bills] line needs id|name|beat|line: {line[:60]!r}")
            continue
        k, v = (p.strip() for p in line.split("|", 1))
        if k and v:
            out[section][k.lower()] = v
    return out


# ---------------------------------------------------------------- rows


def own_pages(pages: list) -> list:
    return [p[len(SITE) + 1:] for p in pages or [] if p.startswith(SITE + "/")]


def stage_of(b: dict, today: _dt.date):
    amdt = is_amdt(b.get("type"))
    table = AMDT_STAGES if amdt else STAGES
    label, step, tone = table.get(b.get("stage") or "", ("Submitted", 0, "quiet") if amdt else ("Introduced", 1, "quiet"))
    end = CONGRESS_ENDS.get(int(b.get("congress") or 0))
    if end and today >= _dt.date.fromisoformat(end) and b.get("stage") != "Law":
        return (f"Expired with the {b.get('congress')}th Congress", step, "quiet", True)
    return (label, step, tone, False)


def read_rows(today: _dt.date) -> tuple:
    if not DATA.exists():
        sys.exit(f"{DATA} not found; run with --fetch and HILL_KEY set first")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    lines = load_lines()
    rows, missing = [], []
    for bid, b in (data.get("bills") or {}).items():
        topic = (b.get("topic") or "-").strip()
        if bid not in lines["bills"]:
            missing.append(f"{bid} ({b.get('label')})")
            continue
        name, beat, line = lines["bills"][bid]
        beat = beat or lines["cases"].get(topic.lower(), "")
        if not beat:
            missing.append(f"{bid} ({b.get('label')}): no beat for topic {topic!r}")
            continue
        label, step, tone, expired = stage_of(b, today)
        moves = b.get("moves") or []
        last = moves[0] if moves else None
        latest = b.get("latest") or {}
        latest_text = clean(latest.get("text")) if latest.get("text") else ""
        rows.append({
            "id": bid, "bill": id_text(b), "type": b.get("type") or "", "name": name or b.get("label") or bid,
            "official": b.get("title") or "", "url": b.get("url") or "", "text_url": b.get("text_url") or "",
            "beat": beat, "pages": own_pages(b.get("pages")), "introduced": b.get("introduced") or "",
            "stage": label, "step": step, "tone": tone, "expired": expired, "amdt": is_amdt(b.get("type")),
            "line": line, "amended": b.get("amended_bill") or None,
            "last_date": last["date"] if last else "", "last": clean(last["text"]) if last else "",
            "latest_date": latest.get("date") or "", "latest": latest_text,
            "moves": [(m["date"], clean(m["text"])) for m in moves],
        })
    for m in missing:
        warn(f"no line in data/congress_lines.txt for {m}; left off the tracker")
    riders = []
    for h in data.get("hits") or []:
        why = lines["riders"].get((h.get("phrase") or "").lower())
        if not why:
            warn(f"no [riders] line for {h.get('phrase')!r}; hit left off the tracker")
            continue
        name = lines["bills"].get(h.get("bill_id") or "", ("", "", ""))[0]
        riders.append({**h, "label": name or h.get("label") or h.get("bill"), "why": why,
                       "pages": own_pages(h.get("pages"))})
    riders.sort(key=lambda r: (r.get("first_seen") or r.get("version_date") or ""), reverse=True)
    return rows, riders


# ---------------------------------------------------------------- rendering


CSS = """
  <style>
    /* Congress Tracker, scoped to this page. Rebuilt by scripts/generate_congress_board.py. */
    .tracker-updated { font-family: var(--mono); font-size: 0.88rem; letter-spacing: 0.04em; text-transform: uppercase; color: var(--accent); border-top: 2px solid var(--accent); border-bottom: 1px solid var(--rule); padding: 10px 0; margin: 28px 0 8px; }
    .article-body p.ct-band { font-family: var(--mono); font-size: 0.9rem; line-height: 1.6; color: var(--ink-soft); margin: 0 0 28px; }
    .article-body p.ct-band b { color: var(--ink); font-weight: 600; }
    .article-body h2.ct-h { font-family: var(--mono); font-size: 0.9rem; font-weight: 500; letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-soft); border-bottom: 1px solid var(--rule); padding-bottom: 8px; margin: 48px 0 0; }
    .article-body h3.ct-beat { font-size: 1.5rem; line-height: 1.25; margin: 44px 0 0; padding-bottom: 8px; border-bottom: 2px solid var(--ink); display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: baseline; }
    .ct-beat .ct-count { font-family: var(--mono); font-size: 0.8rem; font-weight: 400; letter-spacing: 0.06em; color: var(--ink-soft); margin-left: auto; }
    .ct-row { padding: 20px 0 18px; border-bottom: 1px solid var(--rule-soft); }
    .ct-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; }
    .article-body .ct-name { font-size: 1.2rem; line-height: 1.3; margin: 0; font-weight: 600; }
    .ct-pill { font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; padding: 1px 7px; border: 1px solid currentColor; white-space: nowrap; }
    .ct-live { color: #2f5d3f; }
    .ct-watch { color: var(--accent); }
    .ct-quiet { color: var(--ink-muted); }
    .ct-moved { font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.08em; padding: 1px 7px; background: var(--bg-alt); color: var(--ink); }
    .ct-num { font-family: var(--mono); font-size: 0.85rem; color: var(--ink-soft); margin-left: auto; white-space: nowrap; }
    .article-body p.ct-official { font-family: var(--mono); font-size: 0.8rem; color: var(--ink-muted); margin: 2px 0 0; }
    .article-body ol.ct-track { list-style: none; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 3px; margin: 10px 0 0; padding: 0; max-width: 420px; }
    .article-body .ct-track li { margin: 0; padding: 0; height: 5px; background: var(--rule-soft); }
    .article-body .ct-track li.on { background: var(--accent); }
    .article-body .ct-track.law li.on { background: #2f5d3f; }
    .article-body p.ct-steps { font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-muted); margin: 4px 0 0; }
    .article-body p.ct-line { margin: 12px 0 0; }
    .ct-facts { margin: 12px 0 0; display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 4px 14px; font-size: 0.98rem; line-height: 1.55; }
    .ct-facts dt { font-family: var(--mono); font-size: 0.75rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-soft); padding-top: 3px; }
    .ct-facts dd { margin: 0; color: var(--ink); }
    .ct-facts dd time { font-family: var(--mono); font-size: 0.9rem; color: var(--ink); }
    .article-body p.ct-links { margin: 10px 0 0; display: flex; flex-wrap: wrap; gap: 4px 18px; font-family: var(--mono); font-size: 0.85rem; }
    .ct-hist { margin-top: 10px; }
    .ct-hist > summary { cursor: pointer; font-family: var(--mono); font-size: 0.8rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-soft); display: inline-block; border: 1px solid var(--rule); padding: 3px 9px; }
    .ct-hist > summary:hover { color: var(--ink); border-color: var(--ink-soft); }
    .ct-hist > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .ct-hist .ct-facts { margin-top: 8px; }
    .ct-facts dt time { display: block; font-family: var(--mono); font-size: 0.82rem; letter-spacing: 0; text-transform: none; color: var(--ink-soft); margin-top: 2px; }
    .ct-list { margin: 10px 0 0; display: grid; grid-template-columns: 130px minmax(0, 1fr); }
    .ct-list dt, .ct-list dd { margin: 0; padding: 10px 0; border-bottom: 1px solid var(--rule-soft); }
    .ct-list dd { font-size: 1rem; line-height: 1.55; padding-left: 14px; }
    .ct-list time { display: block; font-family: var(--mono); font-size: 0.82rem; color: var(--ink-soft); padding-top: 3px; }
    .ct-list .ct-beat-tag { display: block; font-family: var(--mono); font-size: 0.7rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-muted); margin: 2px 0 0; }
    .article-body .ct-moves { margin: 10px 0 0; }
    .article-body .ct-moves li { font-size: 1rem; line-height: 1.55; padding: 12px 0; border-bottom: 1px solid var(--rule-soft); }
    .article-body .ct-moves li:last-child { border-bottom: 1px solid var(--rule-soft); }
    .ct-moves time { display: inline; margin: 0; font-size: 0.82rem; color: var(--ink-soft); }
    .ct-moves .ct-beat-tag { display: inline; font-family: var(--mono); font-size: 0.7rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-muted); margin-left: 12px; }
    .ct-moves .ct-beat-tag::after { content: ""; display: block; margin-bottom: 4px; }
    .ct-moves strong a { color: var(--ink); text-decoration: none; }
    .ct-moves strong a:hover { text-decoration: underline; }
    .ct-list blockquote { margin: 8px 0 0; padding: 6px 12px; border-left: 3px solid var(--rule); font-family: var(--mono); font-size: 0.85rem; line-height: 1.55; color: var(--ink-soft); }
    .article-body .ct-note { font-family: var(--mono); font-size: 0.85rem; line-height: 1.6; color: var(--ink-soft); margin: 14px 0 0; }
    @media (max-width: 760px) {
      .ct-num, .ct-beat .ct-count { margin-left: 0; width: 100%; }
      .ct-facts { grid-template-columns: 1fr; gap: 2px; }
      .ct-facts dd { margin-bottom: 8px; }
      .ct-list { grid-template-columns: 1fr; }
      .ct-list dt { border-bottom: 0; padding-bottom: 0; }
      .ct-list dd { padding-left: 0; padding-top: 4px; }
    }
  </style>
"""


def anchor(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


_TITLES: dict = {}


def page_title(slug: str) -> str:
    """The <h1> of one of this site's pages, for a link label; the slug when the page is not here."""
    if slug not in _TITLES:
        f = ROOT / f"{slug}.html"
        t = ""
        if f.exists():
            m = re.search(r"<h1[^>]*>(.*?)</h1>", f.read_text(encoding="utf-8"), re.S)
            t = " ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).split()) if m else ""
        _TITLES[slug] = t or slug.replace("-", " ")
    return _TITLES[slug]


def render_row(r: dict, today: _dt.date) -> str:
    h = [f'<div class="ct-row" id="{esc(r["id"])}">']
    h.append(f'<div class="ct-head"><h4 class="ct-name">{esc(r["name"])}</h4>'
             f'<span class="ct-pill ct-{r["tone"]}">{esc(r["stage"])}</span>')
    if r["last_date"] and not r["expired"] and (today - _dt.date.fromisoformat(r["last_date"])).days <= RECENT_DAYS:
        h.append('<span class="ct-moved">MOVED</span>')
    h.append(f'<span class="ct-num">{esc(r["bill"])}</span></div>')
    if r["official"] and r["official"].lower() not in r["name"].lower():
        h.append(f'<p class="ct-official">{esc(r["official"])}</p>')
    if not r["amdt"] and not r["expired"]:
        law = " law" if r["step"] >= 6 else ""
        dots = "".join('<li class="on"></li>' if i < r["step"] else "<li></li>" for i in range(len(TRACK)))
        h.append(f'<ol class="ct-track{law}" aria-label="Step {r["step"]} of {len(TRACK)}: {esc(r["stage"])}">{dots}</ol>'
                 f'<p class="ct-steps" aria-hidden="true">Step {r["step"]} of {len(TRACK)} &middot; {esc(TRACK[r["step"] - 1])}</p>')
    h.append(f'<p class="ct-line">{esc(r["line"])}</p>')
    facts = []
    if r["amended"]:
        a = r["amended"]
        facts.append(f'<dt>Filed to</dt><dd><a href="{esc(a.get("url"))}" rel="noopener">{esc(a.get("label"))}</a>'
                     + (f', {esc(a.get("title"))}' if a.get("title") else "") + "</dd>")
    if r["last_date"]:
        facts.append(f'<dt>Last move<br><time datetime="{r["last_date"]}">{short(r["last_date"])}</time></dt><dd>{esc(r["last"])}</dd>')
    if r["latest"] and r["latest_date"] and (r["latest_date"], r["latest"]) != (r["last_date"], r["last"]):
        facts.append(f'<dt>Latest action<br><time datetime="{r["latest_date"]}">{short(r["latest_date"])}</time></dt><dd>{esc(r["latest"])}</dd>')
    if r["introduced"]:
        facts.append(f'<dt>{"Submitted" if r["amdt"] else "Introduced"}</dt><dd><time datetime="{r["introduced"]}">{long_(r["introduced"])}</time></dd>')
    if facts:
        h.append('<dl class="ct-facts">' + "".join(facts) + "</dl>")
    if len(r["moves"]) > 1:
        items = "".join(f'<dt><time datetime="{d}">{short(d)}</time></dt><dd>{esc(t)}</dd>' for d, t in r["moves"])
        h.append(f'<details class="ct-hist"><summary>Key moves ({len(r["moves"])})</summary><dl class="ct-facts">{items}</dl></details>')
    links = [f'<a href="{esc(r["url"])}" rel="noopener">{esc(r["bill"])} on Congress.gov</a>']
    if r["text_url"]:
        links.append(f'<a href="{esc(r["text_url"])}" rel="noopener">Newest text (GovInfo)</a>')
    for p in r["pages"][:2]:
        links.append(f'<a href="{esc(p)}">{esc(page_title(p))}</a>')
    h.append('<p class="ct-links">' + "".join(links) + "</p></div>")
    return "\n".join(h)


def render_rider(x: dict) -> str:
    when = x.get("version_date") or x.get("first_seen") or ""
    t = f'<time datetime="{when}">{short(when)}</time>' if when else "<time></time>"
    verb = "dropped" if x["removed"] else "carries"
    version = f' ({esc(x["version_type"])})' if x.get("version_type") else ""
    body = [f'<a href="{esc(x.get("link") or x.get("bill_url"))}" rel="noopener">{esc(x["label"])}</a>{version} '
            f'{verb} &ldquo;{esc(x["phrase"])}.&rdquo; {esc(x["why"])}']
    if x.get("snippet") and not x["removed"]:
        body.append(f'<blockquote>{esc(x["snippet"])}</blockquote>')
    return f"<dt>{t}</dt><dd>{''.join(body)}</dd>"


def render_main(rows: list, riders: list, today: _dt.date, stamp: str) -> str:
    live = [r for r in rows if not r["expired"]]
    recent = [r for r in live if r["last_date"] and (today - _dt.date.fromisoformat(r["last_date"])).days <= RECENT_DAYS]
    beats = {}
    for r in rows:
        beats.setdefault(r["beat"], []).append(r)
    furthest = sorted([r for r in live if not r["amdt"]], key=lambda r: (r["step"], r["last_date"]), reverse=True)[:1]
    passed = [r for r in live if r["step"] >= 4]

    band = [f"<b>{len(rows)}</b> bills and amendments across <b>{len(beats)}</b> beats."]
    band.append(f"<b>{len(recent)}</b> moved in the last {RECENT_DAYS} days." if recent
                else f"None moved in the last {RECENT_DAYS} days.")
    if passed:
        band.append(f"<b>{len(passed)}</b> {'has' if len(passed) == 1 else 'have'} passed at least one chamber.")

    h = ['<main id="main">', '    <div class="container">', '      <article class="article">', "        <header>",
         '          <p class="article-eyebrow">Tracker &middot; Congress</p>',
         f"          <h1>{esc(H1)}</h1>",
         "          <p class=\"article-dek\">Every federal bill we follow that would change a mass tort, an AI liability fight, "
         "litigation funding or qui tam practice. Each row gives the bill's stage, its last committee or floor move, "
         "and what it would do to the litigation, rebuilt from Congress.gov three times a day.</p>",
         '          <p class="article-meta">Compiled by <a href="david-meldofsky">David Meldofsky</a></p>',
         "        </header>",
         f'        <p class="tracker-updated">Last updated {long_(stamp)} &middot; {len(rows)} bills &middot; {len(beats)} beats</p>',
         '        <p class="li-letter-top"><strong>The monthly letter</strong> carries the month&rsquo;s moves on this tracker, '
         'the Panel&rsquo;s biggest docket moves and the MDLs you follow, once a month. <a href="newsletter">Sign up &rarr;</a></p>',
         '        <div class="article-body">',
         "          <p>Most of what Congress does to mass tort litigation happens out of sight. A preemption clause rides in a farm bill. "
         "A liability shield is filed as an amendment to the defense bill. A funding disclosure rule surfaces as a "
         "stand-alone bill and stalls in committee. This tracker follows each of them from introduction to enactment.</p>",
         "          <p>The stage and the last move come from Congress.gov. The last move is the newest action that changes where a bill stands: "
         "a committee report, a floor vote, a cloture vote, presentment or signature. Referrals and floor procedure are left out. "
         "The one-line read on each bill is ours. For the same bills written for injured readers, see "
         '<a href="https://lawsuitinformer.com/mass-tort-bills-in-congress">Lawsuit Informer&rsquo;s Congress board</a>. '
         'For state statutes and bills, see the <a href="state-law-tracker">State Law Tracker</a>.</p>',
         f'          <p class="ct-band">{" ".join(band)}</p>']

    log = update_log(rows, today)
    if log:
        h.append('          <h2 class="ct-h" id="latest-moves">Latest moves</h2>')
        h.append(f'          <ul class="dev-list ct-moves" data-page="{SLUG}">\n'
                 + "\n".join("            " + render_log_item(d, r, lead) for d, r, lead in log)
                 + "\n          </ul>")
    if riders:
        h.append('          <h2 class="ct-h" id="riders">Riders in must-pass bills</h2>')
        h.append('          <p class="ct-note">Hill Wire reads the newest text of the farm bill, both NDAAs and the Interior-Environment '
                 "appropriations bill for liability language tied to these beats. A phrase that a newer version drops is listed too.</p>")
        h.append('          <dl class="ct-list">' + "".join(render_rider(x) for x in riders) + "</dl>")

    h.append('          <h2 class="ct-h" id="by-beat">Bills by beat</h2>')

    def beat_key(item):
        rs = item[1]
        return (max(r["step"] for r in rs if not r["expired"]) if any(not r["expired"] for r in rs) else 0,
                max((r["last_date"] or r["introduced"] or "") for r in rs))

    for beat, rs in sorted(beats.items(), key=beat_key, reverse=True):
        rs.sort(key=lambda r: (not r["expired"], r["step"], r["last_date"] or r["introduced"] or ""), reverse=True)
        n = f"{len(rs)} {'bill' if len(rs) == 1 else 'bills'}"
        h.append(f'          <h3 class="ct-beat" id="{anchor(beat)}">{esc(beat)}<span class="ct-count">{n}</span></h3>')
        h += [render_row(r, today) for r in rs]

    expired = [r for r in rows if r["expired"]]
    if expired:
        h.append(f'          <p class="ct-note">{len(expired)} of these bills expired with their Congress. They stay listed until a successor bill is filed or the beat is retired.</p>')
    h.append('          <p class="ct-note">Method. Hill Wire polls Congress.gov for each bill three times a day and reads must-pass bill text from GovInfo. '
             "This page is rebuilt from that record and changes only when a stage, an action or a text hit changes. "
             'Corrections go to <a href="mailto:editor@lawsuitintelligencer.com">editor@lawsuitintelligencer.com</a>.</p>')
    h += ["",
          '          <aside class="li-letter" aria-labelledby="li-letter-h">',
          '            <p class="li-letter-h" id="li-letter-h">The Monthly Letter</p>',
          "            <p class=\"li-letter-lead\">Once a month: the bills that moved on this tracker, the Panel&rsquo;s biggest docket moves, and a section on each MDL you follow.</p>",
          '            <form class="li-letter-form" method="post" action="/api/subscribe">',
          '              <label class="li-letter-label" for="li-letter-email">Email address</label>',
          '              <div class="li-letter-row">',
          '                <input type="email" id="li-letter-email" name="email" required autocomplete="email" maxlength="254">',
          '                <button type="submit">Send me the letter</button>',
          "              </div>",
          f'              <input type="hidden" name="source" value="{SLUG}">',
          '              <p class="li-letter-trap" aria-hidden="true"><label>Leave this empty <input type="text" name="bot-field" tabindex="-1" autocomplete="off"></label></p>',
          "            </form>",
          '            <p class="li-letter-note">You confirm by email before anything is sent. Pick the MDLs you follow on the <a href="newsletter">signup page</a> or from any issue.</p>',
          "          </aside>",
          "        </div>",
          "",
          "        <footer>",
          '          <p class="article-byline-end"><a href="david-meldofsky">David Meldofsky</a> is a California-licensed attorney and the editor of Lawsuit Intelligencer. '
          'He is also the founder of <a href="https://lawsuitinformer.com">Lawsuit Informer</a> and <a href="https://lawsuit.center">Lawsuit Center</a>.</p>',
          "        </footer>",
          "      </article>",
          "    </div>",
          "",
          '    <section class="section">',
          '      <div class="container">',
          '        <p class="section-eyebrow center">More from Lawsuit Intelligencer</p>',
          '        <div class="recent">',
          "          <ol>",
          "            <li>",
          "              <div>",
          '                <h3><a href="state-ai-enforcement-tracker">State AI Enforcement Tracker</a></h3>',
          '                <p class="dek">Every state consumer-harm action against a generative AI company, with the court, the statutory theory, the counts as pleaded, the remedy sought, and where each matter stands.</p>',
          '                <p class="meta">Compiled by David Meldofsky</p>',
          "              </div>",
          "            </li>",
          "            <li>",
          "              <div>",
          '                <h3><a href="mass-tort-map">The 2026 Mass Tort Map</a></h3>',
          '                <p class="dek">The federal docket has never been more concentrated, and 2026 is the year the procedural phase finally turned into verdicts. Two of the defense bar&rsquo;s most dependable shields are cracking at the same time: Section&nbsp;230 and bankruptcy.</p>',
          '                <p class="meta">By David Meldofsky</p>',
          "              </div>",
          "            </li>",
          "          </ol>",
          "        </div>",
          "      </div>",
          "    </section>",
          "  </main>"]
    return "\n".join(h)


def rows_digest(rows: list, riders: list) -> str:
    sig = json.dumps({"rows": rows, "riders": riders}, sort_keys=True, default=str)
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the page shell


def ld_block(desc: str, published: str, stamp: str) -> str:
    ld = {
        "@context": "https://schema.org", "@type": "Article", "headline": OG_TITLE, "description": desc,
        "datePublished": published, "dateModified": stamp,
        "author": {"@type": "Person", "@id": "https://davidmeldofsky.com/#person", "name": "David Meldofsky",
                   "url": f"{SITE}/david-meldofsky", "jobTitle": "California-licensed attorney"},
        "publisher": {"@type": "Organization", "name": "Lawsuit Intelligencer", "url": f"{SITE}/"},
        "mainEntityOfPage": {"@type": "WebPage", "@id": f"{SITE}/{SLUG}"},
    }
    body = json.dumps(ld, indent=2, ensure_ascii=False)
    return '<script type="application/ld+json">\n  ' + body.replace("\n", "\n  ") + "\n  </script>"


def first_shell(today: _dt.date) -> str:
    if not SHELL_FROM.exists():
        sys.exit(f"{OUT.name} does not exist and {SHELL_FROM.name} is not here to copy its shell from")
    s = SHELL_FROM.read_text(encoding="utf-8")
    s = s.replace(f"{SITE}/state-ai-enforcement-tracker\"", f"{SITE}/{SLUG}\"")
    # The tracker's own page styles go; this page brings its own in render.
    s = re.sub(r"\n  <style>.*?</style>\n", lambda _: CSS, s, count=1, flags=re.S)
    s = re.sub(r'<script type="application/ld\+json">.*?</script>',
               lambda _: ld_block("", today.isoformat(), today.isoformat()), s, count=1, flags=re.S)
    s = re.sub(r'(<meta property="og:title" content=")[^"]*(")', lambda m: m.group(1) + esc(OG_TITLE) + m.group(2), s, count=1)
    s = re.sub(r'(<meta name="twitter:title" content=")[^"]*(")', lambda m: m.group(1) + esc(OG_TITLE) + m.group(2), s, count=1)
    return s


def set_head(shell: str, stamp: str, desc: str) -> str:
    # The page styles live in the head and change with the generator, so they are rewritten every write.
    # CSS opens with its own newline, so the match's leading newline is not added back (an extra
    # one here grew a blank line in the head on every write).
    shell = re.sub(r"\n+  <style>\s*/\* Congress Tracker, scoped to this page.*?</style>\n", lambda _: CSS, shell,
                   count=1, flags=re.S)
    shell = re.sub(r"<title>.*?</title>", f"<title>{esc(TITLE)}</title>", shell, count=1, flags=re.S)
    for pat in (r'(<meta name="description" content=")[^"]*(")',
                r'(<meta property="og:description" content=")[^"]*(")',
                r'(<meta name="twitter:description" content=")[^"]*(")'):
        shell = re.sub(pat, lambda m: m.group(1) + esc(desc) + m.group(2), shell, count=1)
    m = re.search(r'"datePublished":\s*"(\d{4}-\d{2}-\d{2})"', shell)
    published = m.group(1) if m else stamp
    shell = re.sub(r'<script type="application/ld\+json">.*?</script>',
                   lambda _: ld_block(desc, published, stamp), shell, count=1, flags=re.S)
    return shell


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="refresh data/congress_bills.json from Hill Wire first")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="write even when the rows did not change")
    ap.add_argument("--today", default="", help="YYYY-MM-DD, for tests")
    args = ap.parse_args()
    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()

    if args.fetch:
        fetch(args.dry_run)

    if OUT.exists():
        shell = OUT.read_text(encoding="utf-8")
    else:
        shell = first_shell(today)
        print(f"{OUT.name} is new; shell copied from {SHELL_FROM.name}")
    m = re.search(r"<!-- congress:rows:([0-9a-f]{16}):(\d{4}-\d{2}-\d{2}) -->", shell)
    old_digest, old_stamp = (m.group(1), m.group(2)) if m else ("", "")

    rows, riders = read_rows(today)
    if not rows:
        sys.exit("no rows read; nothing written")
    digest = rows_digest(rows, riders)
    changed = digest != old_digest
    stamp = today.isoformat() if (changed or not old_stamp) else old_stamp

    nbeats = len({r["beat"] for r in rows})
    desc = (f"{len(rows)} federal bills on mass torts, AI liability, litigation funding and qui tam: "
            f"stage, last move and what each would do. Updated {long_(stamp)}.")
    main_html = render_main(rows, riders, today, stamp)
    main_html = main_html.replace('<main id="main">', f'<main id="main">\n<!-- congress:rows:{digest}:{stamp} -->', 1)
    out = re.sub(r'<main id="main">.*?</main>', lambda _: main_html, shell, count=1, flags=re.S)
    out = set_head(out, stamp, desc)

    print(f"{len(rows)} bills in {nbeats} beats, {len(riders)} rider hit(s), digest {digest}"
          + (" (changed)" if changed else " (unchanged)"))
    if args.dry_run:
        for r in rows:
            print(f"  {r['beat'][:30]:<30} {r['bill']:<13} {r['stage']:<22} last {r['last_date'] or '-'}")
        return 0
    if not changed and not args.force:
        print("rows unchanged; file left alone")
        return 0
    OUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUT.name}, stamp {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
