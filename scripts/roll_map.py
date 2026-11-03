#!/usr/bin/env python3
"""Roll mass-tort-map.html to the newest JPML report.

The page carries one data block (<script id="li-data">) that the interactive
layers read, and a set of marked regions, <!-- roll:NAME --> ... <!-- /roll:NAME -->,
that restate the report in static HTML: the summary count, the section 1 lead
sentence, Figs. 1-4, the courthouse paragraphs, the board and trend captions,
the fastest-growing and newest-MDL tables, the stat tiles, the sources
anchor, the masthead issue line, the dates. This script rebuilds the block
from jpml.mdl_pending and rewrites every marked region from it. Everything
outside the markers is the editor's and is never touched, except that one
Update log entry is inserted at the top of the list.

A new MDL needs a name, a type and a seat before it can be placed. The script
lists any it cannot place and stops; add the docket to the block by hand (n,
t, ty, d, j, c, mt) and run again. An MDL that left the report is dropped from
the block.

    python3 scripts/roll_map.py --bq PROJECT           # read BigQuery (ADC)
    python3 scripts/roll_map.py --rows rows.json       # read an exported row file
    python3 scripts/roll_map.py ... --report 2026-11-02   # pin the report (default: newest)
    python3 scripts/roll_map.py ... --dry-run          # print what would change, write nothing

rows.json is {"<report_date>": {"<mdl>": {"p": pending, "h": total_historical,
"dist": seat code, "judge": name, "lit": litigation}}} for at least the two
newest reports. Also refreshes the "Recently updated" list on index.html.
"""
import argparse, datetime as dt, json, re, sys
from zoneinfo import ZoneInfo
from pathlib import Path

MAP, INDEX = "mass-tort-map.html", "index.html"
FIRST_ISSUE = (2026, 5)
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
NBSP = "&nbsp;"

SQL = """
SELECT CAST(report_date AS STRING) rd, mdl_number n, pending_actions p, total_historical h,
       IFNULL(district,'') dist, IFNULL(judge,'') judge, litigation lit
FROM `{p}.jpml.mdl_pending`
WHERE report_date >= DATE_SUB((SELECT MAX(report_date) FROM `{p}.jpml.mdl_pending`), INTERVAL 45 DAY)
"""


def fmt(n):
    return f"{n:,}"


def signed(n):
    return ("+" if n > 0 else "&minus;" if n < 0 else "") + fmt(abs(n))


def longdate(iso, nb=True):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTH[m-1]}{NBSP if nb else ' '}{d}, {y}"


def shortdate(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MON[m-1]}{NBSP}{d}"


def issue_line(iso):
    y, m = (int(x) for x in iso.split("-")[:2])
    n = (y - FIRST_ISSUE[0]) * 12 + (m - FIRST_ISSUE[1]) + 1
    vol = (n - 1) // 12 + 1
    roman = ""
    for v, s in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while vol >= v:
            roman += s; vol -= v
    return f"Vol. {roman} &middot; No. {n} &middot; {MONTH[m-1]} {y}"


# ---------------------------------------------------------------- rows

def read_bq(project):
    from google.cloud import bigquery
    out = {}
    for r in bigquery.Client(project=project).query(SQL.format(p=project)).result():
        out.setdefault(r["rd"], {})[int(r["n"])] = dict(p=int(r["p"]), h=int(r["h"]), dist=r["dist"], judge=r["judge"], lit=r["lit"])
    return out


def read_rows(path):
    raw = json.load(open(path))
    return {rd: {int(k): v for k, v in rows.items()} for rd, rows in raw.items()}


# ---------------------------------------------------------------- block

def region(h, name):
    a, b = f"<!-- roll:{name} -->", f"<!-- /roll:{name} -->"
    i, j = h.find(a), h.find(b)
    if i < 0 or j < 0:
        sys.exit(f"marker missing: {name}")
    return i + len(a), j


def put(h, name, body):
    i, j = region(h, name)
    return h[:i] + body + h[j:]


def load_block(h):
    m = re.search(r'(<script type="application/json" id="li-data">)(.*?)(</script>)', h, re.S)
    return json.loads(m.group(2)), m


def roll_block(d, rows, report):
    dates = sorted(rows)
    prev = [x for x in dates if x < report][-1]
    cur, old = rows[report], rows[prev]
    blk = {x["n"]: x for x in d["dockets"]}
    missing = sorted(set(cur) - set(blk))
    if missing:
        for n in missing:
            r = cur[n]
            print(f"  cannot place MDL {n}: {r['lit'][:70]} · {r['dist']} · {r['judge']} · {r['p']} pending")
        sys.exit("add the dockets above to li-data (n, t, ty, d, j, c, mt) and run again")
    if d["report"] == report:
        print(f"block already carries {report}; regions are rewritten from it")
        return d, report, prev
    if d["report"] != prev:
        sys.exit(f"block carries {d['report']}, prior report is {prev}: roll one report at a time")
    month = report[:7]
    d["prev"], d["report"] = prev, report
    d["months"].append(month)
    d["totals"]["actions"].append(sum(r["p"] for r in cur.values()))
    d["totals"]["mdls"].append(len(cur))
    kept = []
    for x in d["dockets"]:
        n = x["n"]
        if n not in cur:
            print(f"  dropped MDL {n} ({x.get('t')}): left the report")
            continue
        x["p"] = cur[n]["p"]
        x["q"] = old[n]["p"] if n in old else None
        x.pop("order", None)
        if "s" in x:
            x["s"].append(cur[n]["p"]); x["h"].append(cur[n]["h"])
        kept.append(x)
    d["dockets"] = kept
    return d, report, prev


# ---------------------------------------------------------------- figures

def fig1(live, report):
    mega = [r for r in live if r["p"] >= 1000]
    tot, mt = sum(r["p"] for r in live), sum(r["p"] for r in mega)
    ps, pa = 100 * len(mega) / len(live), 100 * mt / tot
    w1, w2 = 608 * ps / 100, 608 * pa / 100
    words = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty", "Twenty-one", "Twenty-two", "Twenty-three", "Twenty-four", "Twenty-five"]
    count = words[len(mega)] if len(mega) < len(words) else str(len(mega))
    return f'''<div class="li-fig" data-fig="1" role="img" aria-label="Concentration bar chart: MDLs with 1,000-plus pending actions are about {ps:.0f} percent of dockets but about {pa:.1f} percent of all pending actions.">
            <div class="li-fig-head">
              <span class="li-fig-num">Fig. 1</span>
              <span class="li-fig-title">Docket concentration &middot; mega-MDLs (1,000+ cases)</span>
            </div>
            <div class="li-fig-body">
              <svg viewBox="0 0 620 240" xmlns="http://www.w3.org/2000/svg">
                <text x="6" y="32" class="li-lbl">Share of active MDLs</text>
                <rect x="6" y="44" width="608" height="30" fill="#e6ddca"/>
                <rect x="6" y="44" width="{w1:.1f}" height="30" class="li-bar"/>
                <text x="{6 + w1 + 12:.0f}" y="64" class="li-val">{ps:.1f}%</text>

                <text x="6" y="124" class="li-lbl">Share of pending actions</text>
                <rect x="6" y="136" width="608" height="30" fill="#e6ddca"/>
                <rect x="6" y="136" width="{w2:.1f}" height="30" class="li-bar"/>
                <text x="{6 + w2 - 10:.0f}" y="156" class="li-valon" text-anchor="end">{pa:.1f}%</text>

                <line x1="6" y1="196" x2="614" y2="196" class="li-grid"/>
                <text x="6" y="220" class="li-axv">0%</text>
                <text x="310" y="220" class="li-axv" text-anchor="middle">50%</text>
                <text x="614" y="220" class="li-axv" text-anchor="end">100%</text>
              </svg>
            </div>
            <p class="li-fig-cap">{count} dockets carry 1,000 or more pending actions. They are {ps:.0f}% of all active MDLs and hold {fmt(mt)} of the {fmt(tot)} pending cases. Source: JPML pending-dockets report, {longdate(report)}.</p>
          </div>
''', len(mega), ps, pa, tot, mt


def fig2(live, report, prev, seats):
    top = sorted(live, key=lambda r: -r["p"])[:10]
    axis = 70000 if top[0]["p"] <= 70000 else 10000 * (top[0]["p"] // 10000 + 1)
    rows = []
    y = 22
    for i, r in enumerate(top):
        w = 608 * r["p"] / axis
        cls = "li-bar-hi" if r.get("mt") and r["n"] in (2738, 3140, 3084, 3047, 3081) else "li-bar"
        if i == 0:
            cls = "li-bar-hi"
        inside = w > 150
        val = (f'<text x="{6 + w - 8:.1f}" y="{y + 24}" class="li-valon" text-anchor="end">{fmt(r["p"])}</text>' if inside
               else f'<text x="{6 + w + 8:.1f}" y="{y + 24}" class="li-val">{fmt(r["p"])}</text>')
        rows.append(f'''                <text x="6" y="{y}" class="li-lbl">{html_esc(r["t"])}</text>
                <rect x="6" y="{y + 8}" width="{w:.1f}" height="22" class="{cls}"/>
                {val}
''')
        y += 58
    base = y - 58 + 48
    tenth = top[9]
    below = sorted([r for r in live if r["p"] < tenth["p"]], key=lambda r: -r["p"])[:3]
    tail = ", ".join(f"{html_esc(r['t'])} at {fmt(r['p'])}" for r in below)
    first = ", ".join(f"{html_esc(r['t'])} at {fmt(r['p'])}" for r in top[:3])
    svg = f'''<div class="li-fig" data-fig="2" role="img" aria-label="Horizontal bar chart of the ten largest MDLs by pending actions as of {longdate(report, nb=False)}. {html_esc(top[0]['t'])} leads at {fmt(top[0]['p'])}, far ahead of {html_esc(top[1]['t'])} at {fmt(top[1]['p'])} and {html_esc(top[2]['t'])} at {fmt(top[2]['p'])}.">
            <div class="li-fig-head">
              <span class="li-fig-num">Fig. 2</span>
              <span class="li-fig-title">Ten largest MDLs &middot; pending actions</span>
            </div>
            <div class="li-fig-body">
              <svg viewBox="0 0 620 {base + 34}" xmlns="http://www.w3.org/2000/svg">
{"".join(rows)}
                <line x1="6" y1="{base}" x2="614" y2="{base}" class="li-grid"/>
                <text x="6" y="{base + 22}" class="li-axv">0</text>
                <text x="310" y="{base + 22}" class="li-axv" text-anchor="middle">{fmt(axis // 2)}</text>
                <text x="614" y="{base + 22}" class="li-axv" text-anchor="end">{fmt(axis)}</text>
              </svg>
            </div>
            <p class="li-fig-cap">Pending actions, JPML report of {longdate(report)}. The tenth place is {html_esc(tenth["t"])} at {fmt(tenth["p"])}. Just below it sit {tail}. Counts are point-in-time and shift monthly; confirm against the current report before citing.</p>
          </div>
'''
    return svg


def html_esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def fig3(d, report):
    x = next(r for r in d["dockets"] if r["n"] == 2738)
    s, months = x["s"], d["months"]
    n = len(s)
    lo = (min(v for v in s if v is not None) // 2000) * 2000
    hi = max(v for v in s if v is not None)
    # 12 px per 1,000 from the baseline grid at y=170 (lo); extend scale so the top fits
    per = 120 / max(10000, ((hi - lo) // 1000 + 1) * 1000)  # px per action
    Y = lambda v: 170 - (v - lo) * per
    X = lambda i: 56 + i * (554 / (n - 1))
    pts = [(X(i), Y(v)) for i, v in enumerate(s)]
    grid = []
    for g in range(0, 6):
        v = lo + g * ((hi - lo) // 1000 + 1) * 1000 / 5
        grid.append(f'<line x1="56" y1="{Y(v):.0f}" x2="610" y2="{Y(v):.0f}" class="li-grid"/>')
    labels = [(lo, "58k" if lo == 58000 else f"{lo//1000}k"), (lo + 6000, f"{(lo+6000)//1000}k"), (lo + 10000, f"{(lo+10000)//1000}k")]
    lab = "".join(f'<text x="50" y="{Y(v)+5:.0f}" class="li-axv" text-anchor="end">{t}</text>' for v, t in labels if Y(v) > 20)
    path = "M 56.0,194 " + " ".join(f"L {px:.1f},{py:.1f}" for px, py in pts) + f" L {pts[-1][0]:.1f},194 Z"
    poly = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    ticks = []
    for i in (0, 5, 10, 15, n - 1):
        if i >= n: continue
        y, m = months[i].split("-")
        t = f"{MON[int(m)-1][0]}'{y[2:]}" if i == 0 else (f"{MON[int(m)-1]}'{y[2:]}" if i == n - 1 else MON[int(m) - 1])
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        ticks.append(f'<text x="{X(i):.1f}" y="216" class="li-axv" text-anchor="{anchor}">{t}</text>')
    first_m, last_m = months[0], months[-1]
    span = f"{MON[int(first_m[5:])-1]}{NBSP}{first_m[:4]}&ndash;{MON[int(last_m[5:])-1]}{NBSP}{last_m[:4]}"
    last3 = [s[-i] - s[-i - 1] for i in (3, 2, 1)]
    return f'''<div class="li-fig" data-fig="3" role="img" aria-label="Line chart of J&J talc pending actions from {MONTH[int(first_m[5:])-1]} {first_m[:4]} to {MONTH[int(last_m[5:])-1]} {last_m[:4]}, rising from about {fmt(round(s[0], -2))} to {fmt(s[-1])}.">
            <div class="li-fig-head">
              <span class="li-fig-num">Fig. 3</span>
              <span class="li-fig-title">J&amp;J Talc (MDL 2738) &middot; pending actions, {span}</span>
            </div>
            <div class="li-fig-body">
              <svg viewBox="0 0 620 230" xmlns="http://www.w3.org/2000/svg">
                {"".join(grid)}
                {lab}
                <path d="{path}" class="li-area"/>
                <polyline points="{poly}" class="li-line"/>
                <circle cx="{pts[0][0]:.0f}" cy="{pts[0][1]:.1f}" r="4" class="li-dot-n"/>
                <circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4.5" class="li-dot"/>
                <text x="62" y="{pts[0][1]-7.5:.0f}" class="li-val">{fmt(s[0])}</text>
                <text x="603" y="{pts[-1][1]-8:.1f}" class="li-val" text-anchor="end">{fmt(s[-1])}</text>
                <line x1="56" y1="194" x2="610" y2="194" class="li-grid"/>
                {"".join(ticks)}
              </svg>
            </div>
            <p class="li-fig-cap">Monthly pending actions, {span}. The steep rise reflects direct filing into the transferee court. The plateau follows the collapse of the proposed bankruptcy settlement. The last three reports added {", then ".join(fmt(v) for v in last3)}. Source: JPML monthly statistics.</p>
          </div>
'''


def fig4(d, report):
    x = next(r for r in d["dockets"] if r["n"] == 3140)
    months = d["months"]
    start = next(i for i, v in enumerate(x["s"]) if v is not None)
    start = max(start, len(months) - 12)
    s = x["s"][start:]
    ms = months[start:]
    n = len(s)
    top = max(s)
    unit = 1000
    gmax = (top // unit + 1) * unit
    per = (194 - 14) / gmax
    Y = lambda v: 194 - v * per
    step = 554 / n
    w = min(36, step - 10)
    gains = [s[i] - s[i - 1] for i in range(1, n)]
    peak = gains.index(max(gains)) + 1
    grid = "".join(f'<line x1="56" y1="{Y(k*unit):.1f}" x2="610" y2="{Y(k*unit):.1f}" class="li-grid"/>' for k in range(1, int(gmax // unit) + 1))
    lab = "".join(f'<text x="50" y="{Y(k*unit)+4.5:.1f}" class="li-axv" text-anchor="end">{k}k</text>' for k in range(1, int(gmax // unit) + 1))
    cols, ticks = [], []
    for i, v in enumerate(s):
        cx = 56 + step * i + step / 2
        cls = "li-bar-hi" if i == peak else "li-bar"
        cols.append(f'<rect x="{cx - w/2:.1f}" y="{Y(v):.1f}" width="{w:.1f}" height="{194 - Y(v):.1f}" class="{cls}"/>')
        if i in (0, peak, n - 1):
            anchor = "start" if i == 0 else "middle"
            cols.append(f'<text x="{cx - w/2 if i == 0 else cx:.1f}" y="{Y(v)-7:.1f}" class="li-val" text-anchor="{anchor}">{fmt(v)}</text>')
        y, m = ms[i].split("-")
        t = f"{MON[int(m)-1]}'{y[2:]}" if i == 0 or (m == "01" and step >= 50) else MON[int(m) - 1]
        ticks.append(f'<text x="{cx:.1f}" y="216" class="li-axv" text-anchor="middle">{t}</text>')
    span = f"{MON[int(ms[0][5:])-1]}{NBSP}{ms[0][:4]}&ndash;{MON[int(ms[-1][5:])-1]}{NBSP}{ms[-1][:4]}"
    rest = ", ".join(fmt(v) for i, v in enumerate(s) if i not in (0, peak, n - 1))
    return f'''<div class="li-fig" data-fig="4" role="img" aria-label="Column chart of Depo-Provera pending actions from {MONTH[int(ms[0][5:])-1]} {ms[0][:4]} to {MONTH[int(ms[-1][5:])-1]} {ms[-1][:4]}, rising from {fmt(s[0])} to {fmt(s[-1])}, with the largest single-report surge marked.">
            <div class="li-fig-head">
              <span class="li-fig-num">Fig. 4</span>
              <span class="li-fig-title">Depo-Provera (MDL 3140) &middot; from surge to settlement</span>
            </div>
            <div class="li-fig-body">
              <svg viewBox="0 0 620 230" xmlns="http://www.w3.org/2000/svg">
                {grid}
                {lab}
                {"".join(cols)}
                <line x1="56" y1="194" x2="610" y2="194" class="li-grid"/>
                {"".join(ticks)}
              </svg>
            </div>
            <p class="li-fig-cap">Monthly pending actions, {span}. The surge peaked with a gain of roughly {fmt(round(max(gains), -2))} cases in a single report (marked). The last report added {fmt(gains[-1])}, to {fmt(s[-1])}. Only the first, peak and final columns are labeled; the rest run {rest}. Source: JPML monthly statistics.</p>
          </div>
'''


# ---------------------------------------------------------------- tables

def growth_table(live, report, prev):
    both = [r for r in live if r.get("q") is not None]
    for r in both:
        r["_d"] = r["p"] - r["q"]
    gains = sorted([r for r in both if r["_d"] > 0], key=lambda r: (-r["_d"], r["n"]))[:10]
    floor = gains[-1]["_d"] if gains else 0
    drops = sorted([r for r in both if r["_d"] < 0 and -r["_d"] >= max(floor, 50)], key=lambda r: (r["_d"], r["n"]))[:5]
    rows = []
    for r in gains:
        pct = f" &middot; +{100*r['_d']/r['q']:.1f}%" if r["q"] else ""
        rows.append(f'                <tr><td>{html_esc(r["t"])} (MDL{NBSP}{r["n"]})</td><td>{fmt(r["q"])}</td><td>{fmt(r["p"])}</td><td class="li-up">+{fmt(r["_d"])}{pct}</td></tr>')
    for r in drops:
        pct = f" &middot; &minus;{100*-r['_d']/r['q']:.1f}%" if r["q"] else ""
        rows.append(f'                <tr><td>{html_esc(r["t"])} (MDL{NBSP}{r["n"]})</td><td>{fmt(r["q"])}</td><td>{fmt(r["p"])}</td><td class="li-down">&minus;{fmt(-r["_d"])}{pct}</td></tr>')
    return f'''<div class="li-table-wrap">
            <table class="li-table">
              <caption>Largest moves &middot; {longdate(prev)} to {longdate(report)}: the ten biggest gains, then the biggest drops</caption>
              <thead>
                <tr><th scope="col">Docket</th><th scope="col">{shortdate(prev)}</th><th scope="col">{shortdate(report)}</th><th scope="col">Change</th></tr>
              </thead>
              <tbody>
{chr(10).join(rows)}
              </tbody>
            </table>
          </div>''', gains, drops


def newest_table(d, live, report, old_html, seats):
    rows = sorted([r for r in live if r.get("c")], key=lambda r: (r["c"], r["n"]), reverse=True)
    cutoff = (dt.date.fromisoformat(report) - dt.timedelta(days=160)).isoformat()
    rows = [r for r in rows if r["c"] >= cutoff] or rows[:10]
    old_court = dict(re.findall(r"\(MDL&nbsp;(\d+)\)</td><td>[^<]*</td><td>([^<]*)</td>", old_html))
    out = []
    for r in rows:
        y, m, dd = r["c"].split("-")
        court = old_court.get(str(r["n"])) or f"{seats.get(r['d'], {}).get('abbr', r['d']).replace(' ', NBSP)} &middot; {html_esc(r.get('j', '').split(',')[0].split()[-1])}"
        out.append(f'                <tr><td>{html_esc(r["t"])} (MDL{NBSP}{r["n"]})</td><td>{MON[int(m)-1]}{NBSP}{int(dd)}</td><td>{court}</td><td>{fmt(r["p"])}</td></tr>')
    first, last = rows[-1]["c"], rows[0]["c"]
    span = f"{MONTH[int(first[5:7])-1]}&ndash;{MONTH[int(last[5:7])-1]} {last[:4]}"
    return f'''<div class="li-table-wrap">
            <table class="li-table">
              <caption>Newest MDLs &middot; centralized {span}</caption>
              <thead>
                <tr><th scope="col">Docket</th><th scope="col">Centralized</th><th scope="col">Court</th><th scope="col">Pending {shortdate(report)}</th></tr>
              </thead>
              <tbody>
{chr(10).join(out)}
              </tbody>
            </table>
          </div>''', len(rows)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bq"); ap.add_argument("--rows"); ap.add_argument("--report")
    ap.add_argument("--path", default="."); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restamp", action="store_true", help="move the dates even when the report did not change")
    a = ap.parse_args()
    if not (a.bq or a.rows):
        sys.exit("give --bq PROJECT or --rows FILE")
    rows = read_bq(a.bq) if a.bq else read_rows(a.rows)
    report = a.report or max(rows)
    root = Path(a.path)
    page = root / MAP
    h = page.read_text(encoding="utf-8")
    d, m = load_block(h)
    old_report = d["report"]
    d, report, prev = roll_block(d, rows, report)
    live = [r for r in d["dockets"] if r.get("p") is not None]
    seats = d["seats"]
    tot = sum(r["p"] for r in live)
    tot_prev = d["totals"]["actions"][-2]
    mdls, mdls_prev = len(live), d["totals"]["mdls"][-2]
    today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    moved = old_report != report

    # block
    h = h[:m.start(2)] + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + h[m.end(2):]

    # figures
    f1, nmega, ps, pa, _, mt_actions = fig1(live, report)
    h = put(h, "fig1", f1)
    h = put(h, "fig2", fig2(live, report, prev, seats))
    h = put(h, "fig3", fig3(d, report))
    h = put(h, "fig4", fig4(d, report))

    # counts in prose
    da, dm = tot - tot_prev, mdls - mdls_prev
    move = ("up" if da > 0 else "down" if da < 0 else "unchanged")
    talc = next(r["p"] for r in live if r["n"] == 2738)
    h = put(h, "summary-count", f"<li>The system holds <strong>{fmt(tot)} pending actions across {mdls} active MDLs</strong> as of the {longdate(report)} report, {move} {fmt(abs(da)) + ' ' if da else ''}from {longdate(prev)}. About <strong>{ps:.0f}% of dockets hold ~{pa:.0f}% of the cases</strong>, and J&amp;J talc alone is {'roughly a third' if 30 <= 100*talc/tot <= 37 else f'{100*talc/tot:.0f}%'} of everything.</li>")
    h = put(h, "lead-count", f"As of the JPML's {longdate(report)} report, the system held {fmt(tot)} pending actions across {mdls} active multidistrict litigations, {'up' if da > 0 else 'down' if da < 0 else 'level'} from {fmt(tot_prev)} across {mdls_prev} a month earlier.")

    # courthouse
    mtl = [r for r in live if r.get("mt")]
    dist = {}
    for r in mtl:
        dist.setdefault(r["d"], []).append(r)
    by_actions = sorted(dist.items(), key=lambda kv: -sum(r["p"] for r in kv[1]))
    by_count = max(dist.items(), key=lambda kv: (len(kv[1]), sum(r["p"] for r in kv[1])))
    def carries(code):
        top2 = sorted(dist[code], key=lambda r: -r["p"])[:2]
        return " and ".join(html_esc(r.get("l") or r["t"]) for r in top2)
    cn = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen"]
    h = put(h, "courthouse-a", f"<p>The Panel&rsquo;s {longdate(report)} report can be read by courthouse. The mass tort dockets, {len(mtl)} of the {mdls} on the report, hold {fmt(sum(r['p'] for r in mtl))} of its {fmt(tot)} pending actions and sit in {len(dist)} districts.</p>")
    four = by_actions[:4]
    h = put(h, "courthouse-b", f"<p>Four courts hold the mass of the system. {seats[four[0][0]]['abbr']} carries {carries(four[0][0])}. {seats[four[1][0]]['abbr']} carries {carries(four[1][0])}, {seats[four[2][0]]['abbr']} carries {carries(four[2][0])}, and {seats[four[3][0]]['abbr']} carries {carries(four[3][0])}. {seats[by_count[0]]['abbr']} holds the most mass tort dockets, {cn[len(by_count[1])] if len(by_count[1]) < len(cn) else len(by_count[1])}.</p>")

    # layer labels
    h = put(h, "map-title", f"pending MDLs by district, {longdate(report)}</span>")
    h = put(h, "map-cap", f"The mass torts view shows the {len(mtl)} mass tort dockets on the report. The All MDLs view shows every docket on it. Source: JPML pending-dockets report, {longdate(report)}.")
    h = put(h, "board-intro", f"The change column compares the {longdate(report)} and {longdate(prev)} reports.")
    h = put(h, "board-title", f"The docket board &middot; {len(mtl)} mass tort MDLs on the {longdate(report)} report</span>")
    h = put(h, "board-all", f">All {mdls}</button>")
    h = put(h, "board-th", f'<th scope="col" data-k="q" class="li-num">{shortdate(prev)}</th>\n                      <th scope="col" data-k="p" class="li-num" aria-sort="descending">{shortdate(report)}</th>')
    pl = sum(1 for r in mtl if r["ty"] == "PL")
    h = put(h, "board-cap", f"Pending actions from the JPML reports of {longdate(prev)} and {longdate(report)}. Mass torts here means the {pl} products liability dockets plus {cn[len(mtl)-pl]}")
    created = sorted([r for r in live if r.get("q") is None], key=lambda r: r["n"])
    gone = sorted(set(rows[prev]) - set(rows[report]))
    cre = (f"A blank {shortdate(prev)} cell marks a docket created after that report. {'They are' if len(created) != 1 else 'It is'} " + ", ".join(f"{html_esc(r['t'])} (MDL{NBSP}{r['n']})" for r in created) + ". ") if created else f"No docket was created between the two reports. "
    gon = (f"{cn[len(gone)].capitalize()} docket{'s' if len(gone) != 1 else ''} left the report between the two months: " + ", ".join(f"MDL{NBSP}{n}" for n in gone) + ".") if gone else "No docket left the report between the two months."
    h = put(h, "board-cap2", f'<p class="li-fig-cap">{cre}{gon}</p>')
    fm = d["months"][0]; lm = d["months"][-1]
    h = put(h, "tiles-intro", f"from the {MONTH[int(fm[5:])-1]}{NBSP}{fm[:4]} report to {MONTH[int(lm[5:])-1]}{NBSP}{lm[:4]}. The figure on each tile is the {shortdate(report)} count;")
    h = put(h, "trend-title", f"Trend lines &middot; pending actions by month, {MON[int(fm[5:])-1]}{NBSP}{fm[:4]}&ndash;{MON[int(lm[5:])-1]}{NBSP}{lm[:4]}</span>")
    h = put(h, "trend-cap", f"{MONTH[int(fm[5:])-1]}{NBSP}{fm[:4]} through {longdate(report)}. Each tile")
    h = put(h, "focus-nojs", f"The docket board above lists every docket&rsquo;s {shortdate(report)} and {shortdate(prev)} counts.")
    h = put(h, "focus-cap", f"{MONTH[int(fm[5:])-1]}{NBSP}{fm[:4]} through {longdate(report)}. The dashed line")

    # tables
    i, j = region(h, "growth-table")
    gt, gains, drops = growth_table(live, report, prev)
    h = put(h, "growth-table", gt)
    i, j = region(h, "newest-table")
    nt, nnew = newest_table(d, live, report, h[i:j], seats)
    h = put(h, "newest-table", nt)

    # tiles, sources, masthead, dates
    pm = MONTH[int(prev[5:7]) - 1]
    h = put(h, "stat-mdls", f'<div class="li-stat"><div class="li-n">{mdls}</div><div class="li-k">active MDLs, {"up" if dm > 0 else "down" if dm < 0 else "unchanged"} from {mdls_prev} in {pm}</div></div>')
    h = put(h, "stat-share", f'<div class="li-stat"><div class="li-n"><span class="li-accent">~{pa:.0f}%</span></div><div class="li-k">of pending actions sit in mega-dockets</div></div>')
    h = put(h, "sources-anchor", f"Case counts are drawn from the JPML's pending-dockets and monthly statistics reports, anchored to the {longdate(report)} report; counts are point-in-time and move monthly. Month-over-month changes compare the {longdate(report)} and {longdate(prev)} editions of the pending-dockets-by-actions-pending report. Centralization dates come from the date-transferred column of the pending-dockets-by-MDL-number report of {longdate(report)}.")
    h = put(h, "sources-block", f"{MONTH[int(fm[5:])-1]}{NBSP}{fm[:4]} through {longdate(report)}. Each layer names its edition in its caption.")
    h = put(h, "issue", f"<strong>{issue_line(report)}</strong>")
    h = put(h, "jump", f'<span class="li-jump-k">Current data &middot; {shortdate(report)} JPML report</span>')
    stamp = moved or a.restamp
    i, j = region(h, "updated-note")
    rest = re.sub(r'^<p class="article-meta">Updated [^,]+, \d{4}, with the Panel&rsquo;s [^.]*\.\s*', "", h[i:j])
    if stamp:
        h = put(h, "updated-note", f'<p class="article-meta">Updated {longdate(today, nb=False)}, with the Panel&rsquo;s {longdate(report)} report. ' + rest.lstrip())
        h, nsub = re.subn(r'"dateModified":\s*"[0-9-]+"', f'"dateModified":"{today}"', h, count=1)
        assert nsub == 1, "no dateModified in the JSON-LD"

    # update log entry (only when the report moved)
    if stamp and f"Panel&rsquo;s {longdate(report)} report" not in h[h.find('<ul class="dev-list"'):]:
        top_gain = gains[0] if gains else None
        entry = (f'              <li id="dev-{today}-{MONTH[int(report[5:7])-1].lower()}-report" data-kind="count"><time datetime="{today}">{MON[int(today[5:7])-1]}. {int(today[8:])}, {today[:4]}</time> '
                 f'<strong>Moved to the Panel&rsquo;s {longdate(report)} report.</strong> {fmt(tot)} pending actions across {mdls} MDLs, {move} {fmt(abs(da)) + " " if da else ""}from {longdate(prev)}. '
                 + (f'{html_esc(top_gain["t"])} added the most, {fmt(top_gain["_d"])}. ' if top_gain else "")
                 + (f'{cn[len(created)].capitalize()} new docket{"s" if len(created) != 1 else ""} carr{"y" if len(created) != 1 else "ies"} a first count. ' if created else "")
                 + (f'{cn[len(gone)].capitalize()} left the report.' if gone else "")).rstrip() + "</li>\n"
        anchor = '<ul class="dev-list" data-page="mass-tort-map">\n'
        assert h.count(anchor) == 1
        h = h.replace(anchor, anchor + entry)

    # index: Recently updated
    idx = root / INDEX
    ih = idx.read_text(encoding="utf-8") if idx.exists() and stamp else ""
    if ih:
        blk = re.search(r'(<div class="recent-updated">.*?<ul>\n)(.*?)(\s*</ul>)', ih, re.S)
        items = [x for x in re.findall(r"\s*<li>.*?</li>", blk.group(2), re.S) if 'href="mass-tort-map"' not in x]
        items.insert(0, f'\n          <li><a href="mass-tort-map">The 2026 Mass Tort Map</a> <time datetime="{today}">{MON[int(today[5:7])-1]} {int(today[8:])}, {today[:4]}</time></li>')
        ih = ih[:blk.start(2)] + "".join(items[:3]).lstrip("\n") + ih[blk.end(2):]

    print(f"report {report} (prev {prev}): {fmt(tot)} actions, {mdls} MDLs; {nmega} mega-dockets {ps:.1f}% / {pa:.1f}%; "
          f"{len(mtl)} mass tort dockets in {len(dist)} districts; {len(gains)} gains + {len(drops)} drops in the table; {nnew} newest rows")
    if a.dry_run:
        print("dry run, nothing written")
        return
    page.write_text(h, encoding="utf-8")
    if ih:
        idx.write_text(ih, encoding="utf-8")
    print("wrote", page, "and", idx if ih else "(index untouched: dates did not move)")


if __name__ == "__main__":
    main()
