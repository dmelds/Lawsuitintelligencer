// Bot check for the monthly letter's signup forms (added 11/11/26).
//
// Every form on the site that posts to /api/subscribe loads this file: the letter box on the
// homepage, on the Mass Tort Map, on the trackers and dockets, and the full form on /newsletter.
// scripts/add_letter_guard.py puts the <script> tag after each form, and the Congress and State
// tracker generators write it themselves.
//
// What it does. The first time a reader touches a form, it asks GET /api/subscribe for a token
// (a timestamp signed by the function). It counts real clicks and key presses on the form. On
// submit it writes three hidden fields, g (the token), ev (that count) and wd (whether the
// browser reports it is driven by automation), and holds the submit until the token is at least
// 2.6 seconds old. The form then posts as it always did, so the reader lands on the function's
// own "Check your email" page.
//
// A script that posts straight to /api/subscribe has no token, and the function answers it with
// a page holding one "Send me the letter" button. A person presses it; a script that posted once
// never does. When the token cannot be fetched, this file lets the form post without it, so the
// reader reaches that same one-button page and is never stuck.
(function () {
  if (window.__liLetterGuard) return;
  window.__liLetterGuard = true;

  function hidden(form, name) {
    var el = form.querySelector('input[type="hidden"][name="' + name + '"]');
    if (!el) {
      el = document.createElement("input");
      el.type = "hidden";
      el.name = name;
      form.appendChild(el);
    }
    return el;
  }

  function guard(form) {
    if (form.__liGuarded) return;
    form.__liGuarded = true;
    var token = "", tokenAt = 0, asked = false, real = 0, sending = false;
    var btn = form.querySelector('button[type="submit"], input[type="submit"]');
    var label = btn ? (btn.tagName === "INPUT" ? btn.value : btn.textContent) : "";

    function ask() {
      if (asked || !window.fetch) return;
      asked = true;
      fetch(form.getAttribute("action") || "/api/subscribe", { headers: { accept: "application/json" }, cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (j) { token = (j && j.g) || ""; tokenAt = Date.now(); })
        .catch(function () { tokenAt = -1; });
    }
    function busy(on) {
      if (!btn) return;
      btn.disabled = on;
      var text = on ? "Sending" : label;
      if (btn.tagName === "INPUT") btn.value = text; else btn.textContent = text;
    }

    ["pointerdown", "touchstart", "keydown", "click"].forEach(function (type) {
      form.addEventListener(type, function (e) { if (e.isTrusted) { real++; ask(); } }, true);
    });
    form.addEventListener("focusin", ask);

    form.addEventListener("submit", function (e) {
      if (!window.fetch) return; // an old browser posts as before and gets the one-button page
      e.preventDefault();
      if (sending) return;
      sending = true;
      busy(true);
      ask();
      var tries = 0;
      (function go() {
        if (!tokenAt && tries++ < 25) { setTimeout(go, 200); return; }
        var wait = token ? 2600 - (Date.now() - tokenAt) : 0;
        if (wait > 0) { setTimeout(go, wait); return; }
        hidden(form, "g").value = token;
        hidden(form, "ev").value = String(real);
        hidden(form, "wd").value = navigator.webdriver === true ? "true" : "false";
        HTMLFormElement.prototype.submit.call(form);
      })();
    });

    // Coming back with the Back button shows the page from cache: free the button again.
    window.addEventListener("pageshow", function () { sending = false; busy(false); });
  }

  function boot() {
    var forms = document.querySelectorAll('form[action="/api/subscribe"]');
    Array.prototype.forEach.call(forms, guard);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
