#!/usr/bin/env python3
"""Generate state-law-tracker.html, the State Law Tracker, from Hill Wire.

The state-legislature sibling of the Congress Tracker, and the trade-side sibling of
Lawsuit Informer's state board (state-laws-on-mass-tort-cases). Every state law and
bill Hill Wire's STATES tab watches, sorted by the question a practitioner asks first:
who can sue under it. Each row gives the stage, the effective date, the last move as
the legislature prints it, and a line on what the statute does to the litigation.

Two inputs:

  data/state_bills.json   Hill Wire's state watch list, saved from the "states" part
                          of its /api/state. `--fetch` refreshes it when HILL_KEY is
                          set (the workflow does this). The file is rewritten only
                          when a bill's stage or moves changed, so a quiet poll
                          commits nothing. Without the key, or when Hill Wire cannot
                          be reached, the saved file is used.
  data/state_lines.txt    The words, kept by hand:
                            [cases]  Hill Wire topic|beat name
                            [bills]  state and bill|name|who can sue|effective date|
                                     what it does to the litigation
                          "Who can sue" is one of: private, state, shield, unread.
                          A bill shows only once it has a [bills] line. Hill Wire's
                          own notes are desk notes and never reach the page.

The four groups, in page order:
  private   an injured person can sue under the statute
  state     the attorney general or an agency enforces it; no private claim confirmed
  shield    the statute narrows or bars a claim
  unread    enforcement has not been read from the enrolled text yet

What a row shows:
  stage       Hill Wire's stage (Introduced, In committee, Reported, Passed <chamber>,
              Passed both, To governor, Law, Vetoed, Failed)
  effective   for a law with a date on its [bills] line: "In force since" or "Takes
              effect", worked out against the day of the run
  last move   the newest step that changes where the bill stands (a committee report,
              a floor vote, a signature, a veto), in the legislature's words. Filings
              and referrals never count, and neither does a step dated in the future.

Latest moves is the page's Update log, built the way the Congress Tracker builds its
own so the monthly letter can read it: a <ul class="dev-list"
data-page="state-law-tracker">, one <li> per bill per day, id dev-YYYY-MM-DD-<row id>,
data-kind "state", and a bold lead sentence naming who acted and on which bill.

The page shell, the styles and the small helpers are the Congress Tracker's
(generate_congress_board.py is imported for them). On the first run the shell is copied
from state-ai-enforcement-tracker.html the same way. The masthead issue line is
check_issue_line.py's (this page is in its EVERGREEN set) and rolls with
roll_issue_line.py.

The page is written only when the rows changed, and then dateModified and the "Last
updated" line move to today.

    python3 scripts/generate_state_board.py --fetch     # refresh, then build
    python3 scripts/generate_state_board.py             # build from saved data
    python3 scripts/generate_state_board.py --dry-run   # print rows, write nothing
    python3 scripts/generate_state_board.py --force     # write even if unchanged
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import generate_congress_board as cb

ROOT = cb.ROOT
SITE = cb.SITE
SLUG = "state-law-tracker"
OUT = ROOT / f"{SLUG}.html"
DATA = ROOT / "data" / "state_bills.json"
LINES = ROOT / "data" / "state_lines.txt"
HILL_URL = cb.HILL_URL
UA = "lawsuitintelligencer state law tracker (+https://lawsuitintelligencer.com)"
TITLE = "State Law Tracker: Who Can Sue Under Each New Statute"
OG_TITLE = "State Law Tracker: Chatbot, Product Safety and Liability Statutes, Sorted by Who Can Sue"
H1 = "State Law Tracker"
RECENT_DAYS = 30
LATEST_MOVES = 10
MOVES_KEPT = 10
MOVES_SINCE = 180

# The shell helpers in generate_congress_board read these names when they run.
cb.SLUG, cb.OUT, cb.TITLE, cb.OG_TITLE, cb.H1 = SLUG, OUT, TITLE, OG_TITLE, H1

warn, esc, short, long_, to_date, anchor, page_title = (cb.warn, cb.esc, cb.short, cb.long_, cb.to_date,
                                                        cb.anchor, cb.page_title)

TRACK = ["Introduced", "Committee", "Reported", "One chamber", "Both chambers", "Law"]
STAGES = {
    "Introduced": ("Introduced", 1, "quiet"),
    "In committee": ("In committee", 2, "quiet"),
    "Reported": ("Reported", 3, "watch"),
    "Passed both": ("Passed both chambers", 5, "live"),
    "To governor": ("Sent to governor", 5, "live"),
    "Law": ("Enacted", 6, "live"),
    "Vetoed": ("Vetoed", 0, "quiet"),
    "Failed": ("Failed", 0, "quiet"),
}

GROUPS = [
    ("private", "An injured person can sue",
     "The statute, or the consumer protection act it plugs into, gives an injured person a claim."),
    ("state", "The state enforces",
     "The attorney general or an agency enforces these. No private right of action is confirmed in any of them."),
    ("shield", "Liability shields",
     "These narrow or bar a claim that plaintiffs plead today."),
    ("unread", "Enforcement not yet read",
     "We have the duties in these and have not read the enforcement section of the enrolled text. Each moves to a group above once we have."),
]
GROUP_TAG = {"private": "Private claim", "state": "State enforces", "shield": "Shield", "unread": "Enforcement unread"}

# Steps that change where a bill stands, and paperwork that does not. The same two rules
# Hill Wire's STATES tab uses (public/index.html) and Informer's generate_state_board.py.
MOVE_TEXT = re.compile(r"^(re-?reported|reported)\b|\breported (out|favorably|with|without)\b|\bpassed\b|\bsigned\b|"
                       r"\bvetoed\b|\bchaptered\b|\bconcurred\b|\benacted\b", re.I)
PAPER_TEXT = re.compile(r"^(introduced|read (the )?first time|first reading|referred to|re-?referred to|prefiled|"
                        r"pre-filed|filed|bill electronically reproduced)", re.I)
PAPER_TAGS = {"introduction", "reading-1", "referral-committee", "referral", "filing"}
VOTE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,3})")


def is_move(a: dict) -> bool:
    t = (a.get("text") or "").strip()
    tags = a.get("classification") or []
    if t and MOVE_TEXT.search(t):
        return True
    if not t or PAPER_TEXT.search(t):
        return False
    return not (tags and all(x in PAPER_TAGS for x in tags))


# ---------------------------------------------------------------- the data file


def trim_states(states: dict) -> dict:
    """The parts of Hill Wire's state watch the tracker reads, with poll times left out so a
    quiet poll produces the same file. Same shape as Informer's data/state_bills.json."""
    bills = {}
    for key, b in (states.get("bills") or {}).items():
        bid = b.get("id") or key
        latest = b.get("latest") or {}
        moves = [{"date": (a.get("date") or "")[:10], "text": (a.get("text") or "").strip(),
                  "chamber": a.get("chamber") or "", "tags": a.get("classification") or []}
                 for a in (b.get("actions") or []) if is_move(a)]
        moves.sort(key=lambda m: m["date"], reverse=True)
        bills[bid] = {
            "id": bid, "state": b.get("state") or "", "state_name": b.get("stateName") or "",
            "session": b.get("session") or "", "identifier": b.get("identifier") or "",
            "label": b.get("label") or "", "title": b.get("title") or "", "topic": b.get("topic") or "-",
            "pages": b.get("pages") or [], "url": b.get("url") or "", "introduced": (b.get("introduced") or "")[:10],
            "stage": b.get("stage") or "",
            "latest": {"date": (latest.get("date") or "")[:10], "text": (latest.get("text") or "").strip()},
            "moves": moves[:MOVES_KEPT],
        }
    return {"source": HILL_URL + "/api/state", "bills": dict(sorted(bills.items()))}


def fetch(dry_run: bool) -> None:
    key = (os.environ.get("HILL_KEY") or "").strip()
    if not key:
        warn(f"HILL_KEY is not set; building from the saved {DATA.name}")
        return
    req = urllib.request.Request(HILL_URL + "/api/state", headers={"X-Wire-Key": key, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            states = (json.loads(r.read().decode("utf-8")) or {}).get("states")
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
    if not states or not states.get("bills"):
        warn("Hill Wire has no saved state watch yet; building from the saved data")
        return
    run = states.get("lastRun") or {}
    print(f"Hill Wire last polled the states {run.get('at', '?')}: {run.get('billCount', '?')} bills, ok={run.get('ok')}")
    # A bill Hill Wire could not refresh on this poll carries no stage yet. Keep the saved
    # copy of it, so one missed poll never blanks a row.
    new = trim_states(states)
    old = None
    if DATA.exists():
        try:
            old = json.loads(DATA.read_text(encoding="utf-8"))
        except ValueError:
            old = None
    if old:
        for bid, b in new["bills"].items():
            if not b["stage"] and bid in (old.get("bills") or {}):
                kept = dict(old["bills"][bid])
                kept.update({"label": b["label"], "topic": b["topic"], "pages": b["pages"]})
                new["bills"][bid] = kept
    if old == new:
        print(f"{DATA.name} unchanged")
        return
    if dry_run:
        print(f"{DATA.name} would change")
        return
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(new, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {DATA.name}")


# ---------------------------------------------------------------- the words


def line_key(state: str, identifier: str) -> str:
    return (state or "").lower() + " " + re.sub(r"[.\s]+", "", (identifier or "").upper())


def load_lines() -> dict:
    out = {"cases": {}, "bills": {}}
    if not LINES.exists():
        sys.exit(f"{LINES} not found")
    section = None
    for raw in LINES.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(cases|bills)\]$", line, re.I)
        if m:
            section = m.group(1).lower()
            continue
        if not section or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if section == "cases":
            if parts[0] and parts[1]:
                out["cases"][parts[0].lower()] = parts[1]
            continue
        if len(parts) < 5 or not parts[0] or not parts[4]:
            warn(f"{LINES.name}: [bills] line needs state and bill|name|who can sue|effective|line: {line[:60]!r}")
            continue
        who = parts[2].lower() or "unread"
        if who not in GROUP_TAG:
            warn(f"{LINES.name}: who-can-sue {parts[2]!r} is not private, state, shield or unread; filed as unread for {parts[0]}")
            who = "unread"
        eff = parts[3]
        if eff and not to_date(eff):
            warn(f"{LINES.name}: effective date {eff!r} is not YYYY-MM-DD; ignored for {parts[0]}")
            eff = ""
        st, _, ident = parts[0].partition(" ")
        out["bills"][line_key(st, ident)] = (parts[1], who, eff, "|".join(parts[4:]))
    return out


# ---------------------------------------------------------------- rows


def tally(text: str) -> str:
    m = VOTE.search(text or "")
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def lead_sentence(r: dict, m: dict) -> str:
    """Who acted, on which bill. States word the same step fifty ways, so the tags Open
    States puts on the action decide the wording wherever they can."""
    t = (m.get("text") or "").strip()
    tags = set(m.get("tags") or [])
    low = t.lower()
    chamber = (m.get("chamber") or "").strip()
    chamber = chamber if chamber and len(chamber) < 20 and chamber.lower() not in ("governor", "executive") else ""
    st, bill = r["state"], r["ident"]
    vote = tally(t)
    if "executive-veto" in tags or "vetoed" in low:
        return f"{st}'s governor vetoed {bill}."
    if "executive-signature" in tags or re.search(r"\bsigned\b|approved by (the )?governor", low):
        return f"{st}'s governor signed {bill}."
    if "became-law" in tags or "chaptered" in low or "enacted" in low:
        return f"{st} enacted {bill}."
    if "executive-receipt" in tags or re.search(r"(delivered|sent|presented|transmitted) to (the )?governor", low):
        return f"{st} lawmakers sent {bill} to the governor."
    if "passage" in tags or re.search(r"\bpassed\b", low):
        who = f"The {st} {chamber}" if chamber else f"A {st} chamber"
        return f"{who} passed {bill}" + (f", {vote}." if vote else ".")
    if tags & {"committee-passage", "committee-passage-favorable"} or re.search(
            r"^(re-?reported|reported)\b|\breported (out|favorably|with|without)\b", low):
        who = f"A {st} {chamber} committee" if chamber else f"A {st} committee"
        return f"{who} reported {bill}."
    if tags & {"failure", "committee-failure"}:
        return f"{st} {bill} failed a vote" + (f", {vote}." if vote else ".")
    if "withdrawal" in tags:
        return f"The sponsor withdrew {st} {bill}."
    return f"{st} {bill} moved."


def move_rank(m: dict) -> int:
    tags = set(m.get("tags") or [])
    low = (m.get("text") or "").lower()
    for i, (tagset, pat) in enumerate([
            ({"executive-signature", "executive-veto", "became-law"}, r"\bsigned\b|\bvetoed\b|chaptered|enacted"),
            ({"executive-receipt"}, r"to (the )?governor"),
            ({"passage"}, r"\bpassed\b"),
            ({"committee-passage", "committee-passage-favorable"}, r"\breported\b")]):
        if tags & tagset or re.search(pat, low):
            return i
    return 9


def read_rows(today: _dt.date) -> list:
    if not DATA.exists():
        sys.exit(f"{DATA} not found; run with --fetch and HILL_KEY set first")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    lines = load_lines()
    rows, missing = [], []
    for bid, b in (data.get("bills") or {}).items():
        num = f"{(b.get('state') or '').upper()} {b.get('identifier') or ''}".strip()
        entry = lines["bills"].get(line_key(b.get("state"), b.get("identifier")))
        topic = (b.get("topic") or "-").strip()
        beat = lines["cases"].get(topic.lower())
        if not entry or not beat:
            missing.append(f"{num} ({b.get('label')})" + ("" if beat else f", no [cases] beat for topic {topic!r}"))
            continue
        name, who, eff, line = entry
        raw = b.get("stage") or ""
        if raw.startswith("Passed ") and raw != "Passed both":
            label, step, tone = raw, 4, "watch"
        else:
            label, step, tone = STAGES.get(raw, (raw or "Introduced", 1, "quiet"))
        moves = [m for m in (b.get("moves") or []) if m.get("date") and m["date"] <= today.isoformat()]
        last = moves[0] if moves else None
        force = ""
        if step == 6 and eff:
            force = ("In force since " if eff <= today.isoformat() else "Takes effect ") + long_(eff)
        own = [p[len(SITE) + 1:] for p in (b.get("pages") or []) if p.startswith(SITE + "/")]
        rows.append({
            "id": re.sub(r"[^a-z0-9]+", "-", num.lower()).strip("-"), "bill": num,
            "ident": b.get("identifier") or "", "state": b.get("state_name") or (b.get("state") or "").upper(),
            "name": name or b.get("label") or b.get("title") or num, "url": b.get("url") or "",
            "beat": beat, "who": who, "line": line, "force": force, "pages": own,
            "introduced": b.get("introduced") or "", "stage": label, "step": step, "tone": tone,
            "last_date": last["date"] if last else "", "last": last["text"].rstrip(". ") if last else "",
            "moves": moves,
        })
    for m in missing:
        warn(f"no line in {LINES.name} for {m}; left off the tracker")
    return rows


def update_log(rows: list, today: _dt.date) -> list:
    floor = today - _dt.timedelta(days=MOVES_SINCE)
    out = []
    for r in rows:
        by_day = {}
        for m in r["moves"]:
            d = to_date(m["date"])
            if d and floor <= d <= today:
                by_day.setdefault(m["date"], []).append(m)
        for d, ms in by_day.items():
            out.append((d, r, lead_sentence(r, min(ms, key=move_rank))))
    out.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
    return out[:LATEST_MOVES]


# ---------------------------------------------------------------- rendering


def render_log_item(d: str, r: dict, lead: str) -> str:
    return (f'<li id="dev-{d}-{esc(r["id"])}" data-kind="state"><time datetime="{d}">{short(d)}</time>'
            f'<span class="ct-beat-tag">{esc(GROUP_TAG[r["who"]])}</span>'
            f'<strong><a href="#{esc(r["id"])}">{esc(lead)}</a></strong> {esc(r["line"])}</li>')


def render_row(r: dict, today: _dt.date) -> str:
    h = [f'<div class="ct-row" id="{esc(r["id"])}">']
    h.append(f'<div class="ct-head"><h4 class="ct-name">{esc(r["name"])}</h4>'
             f'<span class="ct-pill ct-{r["tone"]}">{esc(r["stage"])}</span>')
    if r["last_date"] and (today - _dt.date.fromisoformat(r["last_date"])).days <= RECENT_DAYS:
        h.append('<span class="ct-moved">MOVED</span>')
    h.append(f'<span class="ct-num">{esc(r["bill"])}</span></div>')
    h.append(f'<p class="ct-official">{esc(r["state"])} &middot; {esc(r["beat"])}</p>')
    if r["step"]:
        law = " law" if r["step"] >= 6 else ""
        dots = "".join('<li class="on"></li>' if i < r["step"] else "<li></li>" for i in range(len(TRACK)))
        h.append(f'<ol class="ct-track{law}" aria-label="Step {r["step"]} of {len(TRACK)}: {esc(r["stage"])}">{dots}</ol>'
                 f'<p class="ct-steps" aria-hidden="true">Step {r["step"]} of {len(TRACK)} &middot; {esc(TRACK[r["step"] - 1])}</p>')
    h.append(f'<p class="ct-line">{esc(r["line"])}</p>')
    facts = []
    if r["force"]:
        label = "In force since" if r["force"].startswith("In force since") else "Takes effect"
        facts.append(f'<dt>{label}</dt><dd>{esc(r["force"][len(label) + 1:])}</dd>')
    if r["last_date"]:
        facts.append(f'<dt>Last move<br><time datetime="{r["last_date"]}">{short(r["last_date"])}</time></dt><dd>{esc(r["last"])}</dd>')
    if r["introduced"]:
        facts.append(f'<dt>Introduced</dt><dd><time datetime="{r["introduced"]}">{long_(r["introduced"])}</time></dd>')
    if facts:
        h.append('<dl class="ct-facts">' + "".join(facts) + "</dl>")
    if len(r["moves"]) > 1:
        items = "".join(f'<dt><time datetime="{m["date"]}">{short(m["date"])}</time></dt><dd>{esc(m["text"].rstrip(". "))}</dd>'
                        for m in r["moves"])
        h.append(f'<details class="ct-hist"><summary>Key moves ({len(r["moves"])})</summary><dl class="ct-facts">{items}</dl></details>')
    links = []
    if r["url"]:
        links.append(f'<a href="{esc(r["url"])}" rel="noopener">{esc(r["bill"])} on Open States</a>')
    for p in r["pages"][:2]:
        links.append(f'<a href="{esc(p)}">{esc(page_title(p))}</a>')
    if links:
        h.append('<p class="ct-links">' + "".join(links) + "</p>")
    h.append("</div>")
    return "\n".join(h)


def render_main(rows: list, today: _dt.date, stamp: str) -> str:
    laws = [r for r in rows if r["step"] == 6]
    pending = [r for r in rows if 0 < r["step"] < 6]
    recent = [r for r in rows if r["last_date"] and (today - _dt.date.fromisoformat(r["last_date"])).days <= RECENT_DAYS]
    nstates = len({r["state"] for r in rows})
    private = [r for r in rows if r["who"] == "private"]

    band = [f"<b>{len(laws)}</b> enacted and <b>{len(pending)}</b> pending across <b>{nstates}</b> states."]
    band.append(f"<b>{len(private)}</b> {'gives' if len(private) == 1 else 'give'} an injured person a claim.")
    band.append(f"<b>{len(recent)}</b> moved in the last {RECENT_DAYS} days." if recent
                else f"None moved in the last {RECENT_DAYS} days.")

    h = ['<main id="main">', '    <div class="container">', '      <article class="article">', "        <header>",
         '          <p class="article-eyebrow">Tracker &middot; State legislatures</p>',
         f"          <h1>{esc(H1)}</h1>",
         "          <p class=\"article-dek\">Every state law and bill we follow that touches a mass tort or an AI liability fight, "
         "sorted by who can sue under it. Each row gives the stage, the effective date, the last move and what the statute "
         "does to the litigation, rebuilt from the legislative record three times a day.</p>",
         '          <p class="article-meta">Compiled by <a href="david-meldofsky">David Meldofsky</a></p>',
         "        </header>",
         f'        <p class="tracker-updated">Last updated {long_(stamp)} &middot; {len(rows)} laws and bills &middot; {nstates} states</p>',
         '        <p class="li-letter-top"><strong>The monthly letter</strong> carries the month&rsquo;s moves on this tracker, '
         'the Panel&rsquo;s biggest docket moves and the MDLs you follow, once a month. <a href="newsletter">Sign up &rarr;</a></p>',
         '        <div class="article-body">',
         "          <p>State legislatures moved faster than Congress on chatbots, baby food and hair relaxers in 2025 and 2026. "
         "The statutes they passed split on one question that decides their value to a plaintiff: who holds the claim. "
         "Washington and California gave it to the injured user. New York and Georgia gave it to the attorney general alone.</p>",
         "          <p>The split matters in pleading. A statute with a private right of action is a count. A statute the state alone "
         "enforces is a standard of care that a negligence count can borrow, where the forum allows negligence per se. "
         "A shield statute is an affirmative defense waiting in the answer.</p>",
         "          <p>The stage and the last move come from each "
         "legislature&rsquo;s record through Open States. The read on each statute is ours. For federal bills, see the "
         '<a href="congress-tracker">Congress Tracker</a>. For the same statutes written for injured readers, see '
         '<a href="https://lawsuitinformer.com/state-laws-on-mass-tort-cases">Lawsuit Informer&rsquo;s state board</a>.</p>',
         f'          <p class="ct-band">{" ".join(band)}</p>']

    log = update_log(rows, today)
    if log:
        h.append('          <h2 class="ct-h" id="latest-moves">Latest moves</h2>')
        h.append(f'          <ul class="dev-list ct-moves" data-page="{SLUG}">\n'
                 + "\n".join("            " + render_log_item(d, r, lead) for d, r, lead in log)
                 + "\n          </ul>")

    h.append('          <h2 class="ct-h" id="by-claim">Statutes by who can sue</h2>')
    for key, title, note in GROUPS:
        rs = [r for r in rows if r["who"] == key]
        if not rs:
            continue
        rs.sort(key=lambda r: (r["step"], r["last_date"] or r["introduced"] or ""), reverse=True)
        n = f"{len(rs)} {'statute' if len(rs) == 1 else 'statutes'}"
        h.append(f'          <h3 class="ct-beat" id="{anchor(title)}">{esc(title)}<span class="ct-count">{n}</span></h3>')
        h.append(f'          <p class="ct-note">{esc(note)}</p>')
        h += [render_row(r, today) for r in rs]

    h.append('          <p class="ct-note">Method. Hill Wire polls Open States for each bill three times a day. '
             "This page is rebuilt from that record and changes only when a stage or a move changes. "
             "The tracker lists statutes we have read, so a state missing here may still have one on the subject. "
             'Corrections go to <a href="mailto:editor@lawsuitintelligencer.com">editor@lawsuitintelligencer.com</a>.</p>')
    h += ["",
          '          <aside class="li-letter" aria-labelledby="li-letter-h">',
          '            <p class="li-letter-h" id="li-letter-h">The Monthly Letter</p>',
          "            <p class=\"li-letter-lead\">Once a month: the statutes that moved on this tracker, the Panel&rsquo;s biggest docket moves, and a section on each MDL you follow.</p>",
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
          '                <h3><a href="congress-tracker">Congress Tracker</a></h3>',
          '                <p class="dek">Every federal bill we follow that would change a mass tort, an AI liability fight, litigation funding or qui tam practice, with its stage and its last move.</p>',
          '                <p class="meta">Compiled by David Meldofsky</p>',
          "              </div>",
          "            </li>",
          "            <li>",
          "              <div>",
          '                <h3><a href="state-ai-enforcement-tracker">State AI Enforcement Tracker</a></h3>',
          '                <p class="dek">Every state consumer-harm action against a generative AI company, with the court, the statutory theory, the counts as pleaded, the remedy sought, and where each matter stands.</p>',
          '                <p class="meta">Compiled by David Meldofsky</p>',
          "              </div>",
          "            </li>",
          "          </ol>",
          "        </div>",
          "      </div>",
          "    </section>",
          "  </main>"]
    return "\n".join(h)


def rows_digest(rows: list) -> str:
    return hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="refresh data/state_bills.json from Hill Wire first")
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
        shell = cb.first_shell(today)
        print(f"{OUT.name} is new; shell copied from {cb.SHELL_FROM.name}")
    m = re.search(r"<!-- states:rows:([0-9a-f]{16}):(\d{4}-\d{2}-\d{2}) -->", shell)
    old_digest, old_stamp = (m.group(1), m.group(2)) if m else ("", "")

    rows = read_rows(today)
    if not rows:
        sys.exit("no rows read; nothing written")
    digest = rows_digest(rows)
    changed = digest != old_digest
    stamp = today.isoformat() if (changed or not old_stamp) else old_stamp

    nlaws = sum(1 for r in rows if r["step"] == 6)
    desc = (f"{nlaws} state laws and {len(rows) - nlaws} bills on chatbots, baby food, hair relaxers and pesticides, "
            f"sorted by who can sue. Updated {long_(stamp)}.")
    main_html = render_main(rows, today, stamp)
    main_html = main_html.replace('<main id="main">', f'<main id="main">\n<!-- states:rows:{digest}:{stamp} -->', 1)
    out = re.sub(r'<main id="main">.*?</main>', lambda _: main_html, shell, count=1, flags=re.S)
    out = cb.set_head(out, stamp, desc)

    print(f"{len(rows)} laws and bills, digest {digest}" + (" (changed)" if changed else " (unchanged)"))
    if args.dry_run:
        for r in rows:
            print(f"  {r['who']:<8} {r['bill']:<12} {r['stage']:<22} {r['force'] or '-':<34} last {r['last_date'] or '-'}")
        return 0
    if not changed and not args.force:
        print("rows unchanged; file left alone")
        return 0
    OUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUT.name}, stamp {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
