// Lawsuit Intelligencer monthly letter: the subscriber side.
//
// Same shape as Lawsuit Informer's newsletter.mjs, with one difference: readers follow MDLs by
// number instead of picking update pages. The pick list is never kept here. It is read from the
// JSON block (id="li-data") that the 2026 Mass Tort Map already carries, so every monthly rewrite
// of the map refreshes the list: new MDLs appear, terminated ones drop off.
//
// The list lives in this site's own Blobs store ("newsletter"), one record per address under
// sub/<id>, where <id> is the first 32 hex of the SHA-256 of the lowercased address. The monthly
// send runs in network-ops and reads the list through /api/newsletter/export.
//
// POST /api/subscribe            signup boxes post here (form or fetch). Fields: email, follow
//                                (MDL number, repeatable, optional; the value "mt" follows every
//                                mass tort on the map, now and as the Panel adds them), source,
//                                bot-field. Always
//                                answers "check your email", so the form never tells a stranger
//                                whether an address is on the list.
// GET  /email/confirm?t=         page with a Confirm button; POST to the same URL activates.
//                                A button rather than a bare link, because mail scanners open
//                                every GET link in a message.
// GET  /email/preferences?t=     MDL checkboxes with a filter box; POST saves. Saving with no
//                                MDL keeps the letter (top movers and the month's updates) and
//                                only drops the personal section. Unsubscribing is its own page.
// GET  /email/unsubscribe?t=     page with an Unsubscribe button; POST unsubscribes. The monthly
//                                email's List-Unsubscribe-Post header posts here too (RFC 8058).
// GET  /api/newsletter/export    X-Export-Key -> every active subscriber with follows and
//                                personal links. Read only. With EXPORT_KEY unset it refuses all.
//
// Every personal link carries t = <id>.<mac>, the mac being the first 32 hex of an HMAC-SHA256 of
// the id under NEWSLETTER_SECRET. Nothing about the token is stored; changing the secret voids
// every link already sent.
//
// Netlify variables (all secret except MAIL_FROM and SES_REGION):
//   NEWSLETTER_SECRET       hex, signs the personal links
//   EXPORT_KEY              hex, read key for the network-ops monthly send
//   SES_ACCESS_KEY_ID       IAM user limited to ses:SendEmail (the Informer user works)
//   SES_SECRET_ACCESS_KEY
//   SES_REGION              us-east-2
//   MAIL_FROM               Lawsuit Intelligencer <editor@lawsuitintelligencer.com>
//   NOTIFY_TO               optional; gets a short email each time a reader confirms
// With SES unset, signups still save as pending and the response is unchanged; the confirm mail
// does not go, and the function logs why.

import { getStore } from "@netlify/blobs";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export const config = {
  path: [
    "/api/subscribe",
    "/email/confirm",
    "/email/preferences",
    "/email/unsubscribe",
    "/api/newsletter/export",
  ],
};

const RESEND_GAP_MS = 10 * 60 * 1000; // at most one confirm mail per address per 10 minutes
const RESEND_DAY_MAX = 4; //             and four per rolling day
const FOLLOW_MAX = 60;
const LIST_TTL_MS = 60 * 60 * 1000; //   the map's MDL list is re-read at most hourly
const ADDRESS = "Lawsuit Intelligencer · 6210 Wilshire Blvd. Ste 200 · Los Angeles, CA 90048";

// ---------------------------------------------------------------- helpers

const site = () => (process.env.SITE_URL || process.env.URL || "https://lawsuitintelligencer.com").replace(/\/$/, "");
// globalThis.__nlStore and __nlFetch let the test harness stand in for Blobs and the network.
const store = () => globalThis.__nlStore || getStore({ name: "newsletter", consistency: "strong" });
const fetcher = (...a) => (globalThis.__nlFetch || fetch)(...a);
const hex = (h) => h.digest("hex");

export function idFor(email) {
  return hex(createHash("sha256").update(email.trim().toLowerCase())).slice(0, 32);
}
function mac(id) {
  const secret = process.env.NEWSLETTER_SECRET || "";
  if (!secret) return "";
  return hex(createHmac("sha256", secret).update("v1:" + id)).slice(0, 32);
}
export function tokenFor(id) {
  const m = mac(id);
  return m ? `${id}.${m}` : "";
}
function idFromToken(t) {
  const m = /^([0-9a-f]{32})\.([0-9a-f]{32})$/.exec(t || "");
  if (!m) return null;
  const want = mac(m[1]);
  if (!want || !timingSafeEqual(Buffer.from(m[2]), Buffer.from(want))) return null;
  return m[1];
}
function sameKey(got, want) {
  if (!want || !got || got.length !== want.length) return false;
  return timingSafeEqual(Buffer.from(got), Buffer.from(want));
}

function cleanEmail(v) {
  const e = String(v || "").trim();
  if (e.length < 6 || e.length > 254) return "";
  return /^[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[a-z]{2,}$/i.test(e) ? e : "";
}

async function readBody(req) {
  const type = req.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    const j = await req.json().catch(() => ({}));
    return { get: (k) => j[k], all: (k) => [].concat(j[k] ?? []) };
  }
  const f = await req.formData().catch(() => new FormData());
  return { get: (k) => f.get(k), all: (k) => f.getAll(k) };
}
const wantsJson = (req) =>
  (req.headers.get("accept") || "").includes("application/json") ||
  (req.headers.get("content-type") || "").includes("application/json");

function json(status, data) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n) => Number(n).toLocaleString("en-US");

// ---------------------------------------------------------------- the MDL list, from the map

let listCache = { at: 0, list: null };

// Returns [{n, t, p, mt}] sorted mass torts first, then by pending actions. null when the map
// cannot be read; callers then accept any plausible MDL number and say the list is unavailable.
export async function mdlList() {
  const now = Date.now();
  if (listCache.list && now - listCache.at < LIST_TTL_MS) return listCache.list;
  try {
    const res = await fetcher(`${site()}/mass-tort-map`, { headers: { accept: "text/html" } });
    if (!res.ok) throw new Error(`map ${res.status}`);
    const html = await res.text();
    const m = /<script type="application\/json" id="li-data">([\s\S]*?)<\/script>/.exec(html);
    if (!m) throw new Error("li-data block not found");
    const data = JSON.parse(m[1]);
    const list = (data.dockets || [])
      .filter((d) => Number.isInteger(d.n))
      .map((d) => ({ n: d.n, t: String(d.t || `MDL ${d.n}`), p: Number.isFinite(d.p) ? d.p : null, mt: d.mt ? 1 : 0 }))
      .sort((a, b) => b.mt - a.mt || (b.p ?? -1) - (a.p ?? -1) || a.n - b.n);
    listCache = { at: now, list, report: data.report || "" };
    return list;
  } catch (e) {
    console.log(`newsletter: MDL list unavailable: ${e}`);
    return listCache.list; // a stale list beats none
  }
}

function cleanFollows(raw, list) {
  const known = list ? new Set(list.map((d) => d.n)) : null;
  const out = [];
  for (const v of raw) {
    const n = Number.parseInt(String(v || "").trim(), 10);
    if (!Number.isInteger(n) || n < 1 || n > 9999) continue;
    if (known && !known.has(n)) continue;
    if (!out.includes(n)) out.push(n);
    if (out.length >= FOLLOW_MAX) break;
  }
  return out;
}
// A saved MDL that has since left the Panel's report stays on the record so the monthly letter
// can say it closed. The form only offers what is pending.
function keepClosed(prev, list) {
  if (!list) return [];
  const live = new Set(list.map((d) => d.n));
  return (prev || []).filter((n) => !live.has(n));
}
// "Every mass tort" is kept as a flag, never as 74 numbers, so the follow cap never cuts it and
// an MDL the Panel creates next month is followed without the reader doing anything. Numbers the
// flag already covers are dropped from the explicit list, which keeps only the other MDLs.
export const ALL_TORTS = "mt";
function wantsAllTorts(raw) {
  return raw.some((v) => String(v || "").trim() === ALL_TORTS);
}
function withoutTorts(follows, list) {
  if (!list) return follows;
  const torts = new Set(list.filter((d) => d.mt).map((d) => d.n));
  return follows.filter((n) => !torts.has(n));
}
function followLabel(follows, list, allTorts = false) {
  if (!follows.length && !allTorts) return "the month's top movers and updates";
  const names = new Map((list || []).map((d) => [d.n, d.t]));
  const shown = follows.slice(0, 6).map((n) => (names.has(n) ? `MDL ${n} (${names.get(n)})` : `MDL ${n}`));
  const more = follows.length > 6 ? ` and ${follows.length - 6} more` : "";
  const parts = [];
  if (allTorts) parts.push("every mass tort on the map");
  if (shown.length) parts.push(shown.join(", ") + more);
  return `top movers plus ${parts.join(", plus ")}`;
}

// ---------------------------------------------------------------- pages

function page(status, title, inner) {
  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>${esc(title)} | Lawsuit Intelligencer</title>
<style>
:root{--bg:#f7f3eb;--panel:#fffdf8;--ink:#1a1612;--soft:#4a423a;--muted:#73675a;--rule:#c9bda9;--rule-soft:#ddd2bd;--accent:#8a2419;--accent-soft:#b04434}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 "Spectral",Georgia,"Times New Roman",serif}
main{max-width:600px;margin:0 auto;padding:40px 16px 64px}
.brand{display:block;font:600 24px/1.2 "Spectral",Georgia,serif;color:var(--ink);text-decoration:none;padding-bottom:12px;border-bottom:3px double var(--ink);margin-bottom:28px}
h1{font:500 30px/1.2 "Spectral",Georgia,serif;margin:0 0 14px}
p{margin:0 0 14px}
a{color:var(--accent)}a:hover{color:var(--accent-soft)}
.mono,legend,.small,button,.count,input[type=search]{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.panel{background:var(--panel);border:1px solid var(--rule);padding:18px;margin:0 0 20px}
fieldset{border:0;padding:0;margin:0 0 6px}
legend{font-size:12px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
label.mdl{display:flex;align-items:center;gap:10px;min-height:44px;padding:4px 0;border-bottom:1px solid var(--rule-soft);cursor:pointer}
label.mdl span{flex:1}
label.mdl .count{font-size:12px;color:var(--muted);white-space:nowrap}
label.mdl-all{border-bottom:2px solid var(--rule)}
input[type=checkbox]:disabled{opacity:.6}
input[type=checkbox]{width:20px;height:20px;accent-color:var(--accent);flex:none}
input[type=search]{width:100%;font-size:15px;padding:12px;border:1px solid var(--rule);background:#fff;color:var(--ink);margin:0 0 14px}
details{margin-top:14px}
summary{cursor:pointer;min-height:44px;display:flex;align-items:center;font-size:13px;font-family:"IBM Plex Mono",ui-monospace,monospace;color:var(--accent)}
button{font-size:14px;font-weight:600;background:var(--accent);color:#fff;border:0;padding:14px 20px;min-height:44px;cursor:pointer}
button:hover{background:var(--accent-soft)}
button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.small{font-size:13px;color:var(--muted)}
.none{display:none}
.foot{margin-top:36px;padding-top:16px;border-top:1px solid var(--rule)}
</style>
</head>
<body>
<main>
<a class="brand" href="${site()}/">Lawsuit Intelligencer</a>
${inner}
<p class="small foot">${esc(ADDRESS)}</p>
</main>
</body>
</html>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

const badLink = () =>
  page(
    400,
    "Link not recognized",
    `<h1>This link did not work</h1>
<p>It may have been copied only in part. Open the link again from the most recent email, or sign up again at <a href="${site()}/newsletter">lawsuitintelligencer.com/newsletter</a>.</p>`
  );

function row(d, checked) {
  const count = d.p == null ? "new" : `${fmt(d.p)} pending`;
  const hay = `${d.n} ${d.t}`.toLowerCase();
  return `<label class="mdl" data-q="${esc(hay)}"><input type="checkbox" name="follow" value="${d.n}"${checked ? " checked" : ""}> <span>${esc(d.t)} <span class="small">MDL ${d.n}</span></span> <span class="count">${count}</span></label>`;
}

// The filter is progressive: with scripts off every row shows and the form still saves.
export function followForm(action, follows, list, buttonLabel, allTorts = false) {
  const chosen = new Set(follows);
  if (!list) {
    return `<form method="post" action="${action}">
<div class="panel"><p>The MDL list is not available right now, so your follows cannot be changed this minute. Try again shortly.</p>
${follows.map((n) => `<input type="hidden" name="follow" value="${n}">`).join("")}${allTorts ? `<input type="hidden" name="follow" value="${ALL_TORTS}">` : ""}</div>
</form>`;
  }
  const closed = follows.filter((n) => !list.some((d) => d.n === n));
  const torts = list.filter((d) => d.mt);
  const other = list.filter((d) => !d.mt);
  const openOther = other.some((d) => chosen.has(d.n));
  return `<form method="post" action="${action}" id="follow-form">
<div class="panel">
<label for="mdl-filter" class="small">Filter by name or MDL number</label>
<input type="search" id="mdl-filter" placeholder="e.g. hair relaxer or 3060" autocomplete="off">
<p class="small none" id="mdl-none" role="status">No pending MDL matches that filter.</p>
<fieldset id="mdl-torts">
<legend>Mass torts (${torts.length})</legend>
<label class="mdl mdl-all"><input type="checkbox" name="follow" value="${ALL_TORTS}" id="mdl-all"${allTorts ? " checked" : ""}> <span><strong>Follow every mass tort</strong> <span class="small">including ones the Panel adds later</span></span></label>
${torts.map((d) => row(d, chosen.has(d.n))).join("\n")}
</fieldset>
<details id="mdl-other"${openOther ? " open" : ""}>
<summary>Every other pending MDL (${other.length})</summary>
<fieldset>
<legend>Other MDLs</legend>
${other.map((d) => row(d, chosen.has(d.n))).join("\n")}
</fieldset>
</details>
${closed.map((n) => `<input type="hidden" name="closed" value="${n}">`).join("")}
</div>
<p class="small">Following none still gets you the monthly letter: the Panel's top movers and the month's updates to the map and trackers. Following more than ten MDLs turns your section into one table, with a full entry for each MDL that had a ruling that month.</p>
<button type="submit">${esc(buttonLabel)}</button>
</form>
<script>
(function(){
  var box=document.getElementById("mdl-filter"),none=document.getElementById("mdl-none"),other=document.getElementById("mdl-other");
  if(!box)return;
  var rows=[].slice.call(document.querySelectorAll("label.mdl:not(.mdl-all)"));
  var all=document.getElementById("mdl-all");
  var tortBoxes=[].slice.call(document.querySelectorAll('#mdl-torts input[name="follow"]:not(#mdl-all)'));
  var saved=tortBoxes.map(function(b){return b.checked;});
  // With "every mass tort" on, the single boxes show checked and are disabled, so the form sends
  // the flag alone. Turning it off puts back what the reader had before.
  function syncAll(){
    tortBoxes.forEach(function(b,i){
      if(all.checked){ if(!b.disabled) saved[i]=b.checked; b.checked=true; b.disabled=true; }
      else { b.disabled=false; b.checked=saved[i]; }
    });
  }
  if(all){ syncAll(); all.addEventListener("change",syncAll); }
  box.addEventListener("input",function(){
    var q=box.value.trim().toLowerCase(),shown=0,otherHit=false;
    if(all) all.closest("label").classList.toggle("none",!!q);
    rows.forEach(function(r){
      var hit=!q||r.getAttribute("data-q").indexOf(q)!==-1;
      r.classList.toggle("none",!hit);
      if(hit){shown++;if(q&&other.contains(r))otherHit=true;}
    });
    if(q&&otherHit)other.open=true;
    none.classList.toggle("none",shown>0);
  });
})();
</script>`;
}

// ---------------------------------------------------------------- SES (API v2, SigV4)

const sha256 = (s) => hex(createHash("sha256").update(s, "utf8"));
const hmacRaw = (key, s) => createHmac("sha256", key).update(s, "utf8").digest();

export function signSes({ body, region, keyId, secret, now = new Date() }) {
  const host = `email.${region}.amazonaws.com`;
  const path = "/v2/email/outbound-emails";
  const amzDate = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const day = amzDate.slice(0, 8);
  const signed = "content-type;host;x-amz-date";
  const canonical = [
    "POST",
    path,
    "",
    `content-type:application/json\nhost:${host}\nx-amz-date:${amzDate}\n`,
    signed,
    sha256(body),
  ].join("\n");
  const scope = `${day}/${region}/ses/aws4_request`;
  const toSign = ["AWS4-HMAC-SHA256", amzDate, scope, sha256(canonical)].join("\n");
  let k = hmacRaw("AWS4" + secret, day);
  k = hmacRaw(k, region);
  k = hmacRaw(k, "ses");
  k = hmacRaw(k, "aws4_request");
  const signature = createHmac("sha256", k).update(toSign, "utf8").digest("hex");
  return {
    url: `https://${host}${path}`,
    headers: {
      "content-type": "application/json",
      "x-amz-date": amzDate,
      authorization: `AWS4-HMAC-SHA256 Credential=${keyId}/${scope}, SignedHeaders=${signed}, Signature=${signature}`,
    },
  };
}

async function sendMail(to, subject, html, text) {
  const { SES_ACCESS_KEY_ID: keyId, SES_SECRET_ACCESS_KEY: secret, MAIL_FROM: from } = process.env;
  const region = process.env.SES_REGION || "us-east-2";
  if (!keyId || !secret || !from) {
    console.log("newsletter: SES not configured, mail not sent");
    return false;
  }
  const body = JSON.stringify({
    FromEmailAddress: from,
    Destination: { ToAddresses: [to] },
    Content: {
      Simple: {
        Subject: { Data: subject, Charset: "UTF-8" },
        Body: { Html: { Data: html, Charset: "UTF-8" }, Text: { Data: text, Charset: "UTF-8" } },
      },
    },
  });
  const { url, headers } = signSes({ body, region, keyId, secret });
  let res;
  try {
    res = await fetcher(url, { method: "POST", headers, body });
  } catch (e) {
    console.log(`newsletter: SES unreachable: ${e}`); // the record still saves
    return false;
  }
  if (!res.ok) {
    console.log(`newsletter: SES ${res.status} ${(await res.text()).slice(0, 300)}`);
    return false;
  }
  return true;
}

function mailShell(heading, paras, link, button) {
  const html = `<!doctype html><html><body style="margin:0;background:#efe8d9;padding:24px 12px;font-family:Georgia,'Times New Roman',serif;color:#1a1612">
<div style="max-width:560px;margin:0 auto;background:#f7f3eb;border:1px solid #c9bda9">
<div style="padding:22px 28px;border-bottom:3px double #1a1612;font-size:22px;font-weight:bold">Lawsuit Intelligencer</div>
<div style="padding:28px">
<p style="margin:0 0 14px;font-size:22px">${esc(heading)}</p>
${paras.map((p) => `<p style="margin:0 0 14px;font-size:16px;line-height:1.6">${esc(p)}</p>`).join("\n")}
<a href="${link}" style="display:inline-block;margin-top:8px;background:#8a2419;color:#ffffff;text-decoration:none;padding:13px 20px;font-family:Arial,sans-serif;font-size:15px;font-weight:bold">${esc(button)}</a>
</div>
<div style="padding:18px 28px;border-top:1px solid #c9bda9;font-family:Arial,sans-serif;font-size:12px;line-height:1.6;color:#73675a">${esc(ADDRESS)}</div>
</div></body></html>`;
  const text = `${heading}

${paras.join("\n\n")}

${button}:
${link}

${ADDRESS}`;
  return { html, text };
}

function confirmMail(link, follows, list, allTorts = false) {
  const m = mailShell(
    "Confirm the monthly letter",
    [
      `Someone, hopefully you, asked for Lawsuit Intelligencer's monthly letter at this address, covering ${followLabel(follows, list, allTorts)}.`,
      "Press the button to start it. If you did not ask, ignore this email and nothing will be sent.",
    ],
    link,
    "Confirm the letter"
  );
  return { subject: "Confirm your Lawsuit Intelligencer letter", ...m };
}

function manageMail(link) {
  const m = mailShell(
    "You already get the monthly letter",
    ["This address signed up again on Lawsuit Intelligencer. To change the MDLs you follow, use the button below."],
    link,
    "Change my MDLs"
  );
  return { subject: "Your Lawsuit Intelligencer MDLs", ...m };
}

// The owner's notice when a reader becomes active. Never blocks or fails the reader's request.
async function notify(rec, how, list) {
  const to = (process.env.NOTIFY_TO || "").trim();
  if (!to) return;
  const what = followLabel(rec.follows || [], list, !!rec.allTorts);
  const from = rec.source ? `${site()}/${rec.source}` : "unknown";
  const when = new Date().toISOString();
  const text = `${rec.email} ${how}.\n\nFollows: ${what}\nSigned up from: ${from}\nTime: ${when}`;
  const html = `<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#1a1612">
<p style="margin:0 0 10px"><strong>${esc(rec.email)}</strong> ${esc(how)}.</p>
<p style="margin:0">Follows: ${esc(what)}<br>Signed up from: ${esc(from)}<br>Time: ${esc(when)}</p>
</body></html>`;
  try {
    await sendMail(to, `New Intelligencer subscriber: ${rec.email}`, html, text);
  } catch (e) {
    console.log(`newsletter: notify failed: ${e}`);
  }
}

// ---------------------------------------------------------------- routes

function mayResend(rec, now) {
  const sends = (rec.sends || []).filter((t) => now - t < 24 * 3600 * 1000);
  if (sends.length >= RESEND_DAY_MAX) return false;
  if (sends.length && now - sends[sends.length - 1] < RESEND_GAP_MS) return false;
  return true;
}

async function subscribe(req) {
  const asJson = wantsJson(req);
  const f = await readBody(req);
  const done = () =>
    asJson
      ? json(200, { ok: true })
      : page(
          200,
          "Check your email",
          `<h1>Check your email</h1>
<p>We sent a link to confirm the monthly letter. It starts only after you press the button in that email.</p>
<p class="small">Nothing there in a few minutes? Check your spam folder.</p>
<p><a href="${site()}/">Back to Lawsuit Intelligencer</a></p>`
        );

  if (f.get("bot-field")) return done(); // honeypot: look the same, keep nothing
  const email = cleanEmail(f.get("email"));
  if (!email) {
    return asJson
      ? json(400, { ok: false, error: "email" })
      : page(400, "Check the address", `<h1>That email address did not look right</h1><p>Go back and check it, then try again.</p>`);
  }
  const list = await mdlList();
  const allTorts = wantsAllTorts(f.all("follow"));
  const follows = allTorts ? withoutTorts(cleanFollows(f.all("follow"), list), list) : cleanFollows(f.all("follow"), list);

  const s = store();
  const id = idFor(email);
  const key = `sub/${id}`;
  const now = Date.now();
  const iso = new Date(now).toISOString();
  const rec = (await s.get(key, { type: "json" })) || { email, createdAt: iso, status: "new" };
  const token = tokenFor(id);
  if (!token) {
    console.log("newsletter: NEWSLETTER_SECRET unset, signup not saved");
    return done();
  }

  if (rec.status === "active") {
    // An active address typed again. Follows stay as the owner set them; the owner gets a link
    // to change them, so a stranger can neither read nor change another reader's choices.
    if (mayResend(rec, now)) {
      const m = manageMail(`${site()}/email/preferences?t=${token}`);
      if (await sendMail(rec.email, m.subject, m.html, m.text)) rec.sends = [...(rec.sends || []), now].slice(-8);
      await s.setJSON(key, rec);
    }
    return done();
  }

  // A pending address signing up again adds its new picks to the earlier ones, so an email-only
  // box on the map never wipes the MDLs picked on /newsletter. Removing picks takes the
  // preferences page, which only the address owner can reach.
  const prev = rec.status === "pending" ? rec.follows || [] : [];
  rec.allTorts = allTorts || (rec.status === "pending" && !!rec.allTorts);
  rec.email = email;
  rec.follows = [...prev, ...follows.filter((n) => !prev.includes(n))].slice(0, FOLLOW_MAX);
  if (rec.allTorts) rec.follows = withoutTorts(rec.follows, list);
  rec.status = "pending";
  rec.source = String(f.get("source") || "").replace(/[^a-z0-9-]/gi, "").slice(0, 80);
  rec.updatedAt = iso;
  if (mayResend(rec, now)) {
    const m = confirmMail(`${site()}/email/confirm?t=${token}`, rec.follows, list, rec.allTorts);
    if (await sendMail(email, m.subject, m.html, m.text)) rec.sends = [...(rec.sends || []), now].slice(-8);
  }
  await s.setJSON(key, rec);
  return done();
}

async function confirm(req, id, token) {
  const s = store();
  const key = `sub/${id}`;
  const rec = await s.get(key, { type: "json" });
  if (!rec) return badLink();
  const self = `${site()}/email/confirm?t=${token}`;
  const prefs = `${site()}/email/preferences?t=${token}`;
  const list = await mdlList();

  if (req.method === "POST") {
    if (rec.status !== "active") {
      rec.status = "active";
      rec.confirmedAt = new Date().toISOString();
      rec.updatedAt = rec.confirmedAt;
      await s.setJSON(key, rec);
      await notify(rec, "confirmed", list);
    }
    const pick = (rec.follows || []).length || rec.allTorts
      ? `<p><a href="${prefs}">Change the MDLs you follow</a> · <a href="${site()}/">Back to Lawsuit Intelligencer</a></p>`
      : `<p>You are not following any MDL yet. <a href="${prefs}">Pick the MDLs you follow</a> to add a section on each one.</p>`;
    return page(
      200,
      "You are signed up",
      `<h1>You are signed up</h1>
<p>The letter goes out once a month, after the Panel's report and the map's rewrite. It covers ${esc(followLabel(rec.follows || [], list, !!rec.allTorts))}.</p>
${pick}`
    );
  }
  if (rec.status === "active") {
    return page(
      200,
      "Already confirmed",
      `<h1>You are already signed up</h1><p><a href="${prefs}">Change the MDLs you follow</a> · <a href="${site()}/">Back to Lawsuit Intelligencer</a></p>`
    );
  }
  return page(
    200,
    "Confirm the letter",
    `<h1>Confirm the monthly letter</h1>
<p>You asked for ${esc(followLabel(rec.follows || [], list, !!rec.allTorts))}.</p>
<form method="post" action="${self}"><button type="submit">Confirm the letter</button></form>`
  );
}

async function preferences(req, id, token) {
  const s = store();
  const key = `sub/${id}`;
  const rec = await s.get(key, { type: "json" });
  if (!rec) return badLink();
  const self = `${site()}/email/preferences?t=${token}`;
  const unsub = `${site()}/email/unsubscribe?t=${token}`;
  const list = await mdlList();

  if (req.method === "POST") {
    if (!list) return page(503, "Try again shortly", `<h1>Nothing was changed</h1><p>The MDL list is not available this minute, so your follows stay as they were. <a href="${self}">Try again</a>.</p>`);
    const f = await readBody(req);
    rec.allTorts = wantsAllTorts(f.all("follow"));
    const picked = cleanFollows(f.all("follow"), list);
    const follows = rec.allTorts ? withoutTorts(picked, list) : picked;
    const closed = keepClosed(cleanFollows(f.all("closed"), null), list).filter((n) => (rec.follows || []).includes(n));
    rec.follows = [...follows, ...closed].slice(0, FOLLOW_MAX);
    rec.updatedAt = new Date().toISOString();
    const cameBack = rec.status !== "active";
    if (cameBack) {
      // Reaching this page took a link from our own email, which proves the address.
      rec.status = "active";
      rec.confirmedAt = rec.confirmedAt || rec.updatedAt;
    }
    await s.setJSON(key, rec);
    if (cameBack) await notify(rec, "came back from the preferences page", list);
    return page(
      200,
      "Saved",
      `<h1>Your MDLs are saved</h1><p>The monthly letter will cover ${esc(followLabel(rec.follows, list, rec.allTorts))}.</p>
<p><a href="${self}">Change them again</a> · <a href="${site()}/">Back to Lawsuit Intelligencer</a></p>`
    );
  }

  const state =
    rec.status === "active"
      ? `<p>The letter goes to <strong>${esc(rec.email)}</strong>. Check the MDLs you want a section on and save.</p>`
      : `<p><strong>${esc(rec.email)}</strong> is not getting the letter right now. Pick MDLs, or none, and save to start it.</p>`;
  return page(
    200,
    "Your MDLs",
    `<h1>The MDLs you follow</h1>
${state}
${followForm(self, rec.follows || [], list, "Save my MDLs", !!rec.allTorts)}
<p class="small" style="margin-top:20px"><a href="${unsub}">Unsubscribe from the letter</a></p>`
  );
}

async function unsubscribe(req, id, token) {
  const s = store();
  const key = `sub/${id}`;
  const rec = await s.get(key, { type: "json" });
  if (!rec) return badLink();
  const self = `${site()}/email/unsubscribe?t=${token}`;
  const prefs = `${site()}/email/preferences?t=${token}`;

  if (req.method === "POST") {
    if (rec.status !== "unsubscribed") {
      rec.status = "unsubscribed";
      rec.unsubscribedAt = new Date().toISOString();
      rec.updatedAt = rec.unsubscribedAt;
      await s.setJSON(key, rec);
    }
    if (!(req.headers.get("content-type") || "").includes("form") && !wantsJson(req)) {
      return new Response("", { status: 200 }); // a mail client's one-click POST
    }
    return page(
      200,
      "Unsubscribed",
      `<h1>You are unsubscribed</h1><p>No more monthly letters will go to ${esc(rec.email)}.</p>
<p><a href="${prefs}">Changed your mind? Start it again</a></p>`
    );
  }
  if (rec.status === "unsubscribed") {
    return page(200, "Already unsubscribed", `<h1>You are already unsubscribed</h1><p><a href="${prefs}">Start the letter again</a></p>`);
  }
  return page(
    200,
    "Unsubscribe",
    `<h1>Stop the monthly letter?</h1>
<p>This stops every Lawsuit Intelligencer letter to ${esc(rec.email)}.</p>
<form method="post" action="${self}"><button type="submit">Unsubscribe</button></form>`
  );
}

async function exportList(req) {
  if (!sameKey(req.headers.get("x-export-key") || "", process.env.EXPORT_KEY || "")) {
    return json(401, { error: "unauthorized" });
  }
  const s = store();
  const { blobs } = await s.list({ prefix: "sub/" });
  const out = [];
  const counts = { active: 0, pending: 0, unsubscribed: 0 };
  for (const b of blobs) {
    const rec = await s.get(b.key, { type: "json" });
    if (!rec) continue;
    counts[rec.status] = (counts[rec.status] || 0) + 1;
    if (rec.status !== "active") continue;
    const token = tokenFor(b.key.slice(4));
    out.push({
      email: rec.email,
      follows: rec.follows || [],
      allTorts: !!rec.allTorts,
      confirmedAt: rec.confirmedAt || "",
      preferences: `${site()}/email/preferences?t=${token}`,
      unsubscribe: `${site()}/email/unsubscribe?t=${token}`,
    });
  }
  return json(200, { generated: new Date().toISOString(), counts, report: listCache.report || "", subscribers: out });
}

export default async function handler(req) {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/$/, "");

  if (path === "/api/subscribe") {
    if (req.method !== "POST") return json(405, { error: "method" });
    return subscribe(req);
  }
  if (path === "/api/newsletter/export") {
    if (req.method !== "GET") return json(405, { error: "method" });
    return exportList(req);
  }

  if (req.method !== "GET" && req.method !== "POST") return json(405, { error: "method" });
  const token = url.searchParams.get("t") || "";
  const id = idFromToken(token);
  if (!id) return badLink();
  if (path === "/email/confirm") return confirm(req, id, token);
  if (path === "/email/preferences") return preferences(req, id, token);
  if (path === "/email/unsubscribe") return unsubscribe(req, id, token);
  return json(404, { error: "not found" });
}
