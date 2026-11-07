/* The companion: Kitty's cat in a corner, and a panel that explains the screen you're on.

   What it does: reads the context the page hands it, says it in plain words, and points at things.
   What it can't do, by construction: this file makes no network requests and sends no messages. It has
   no way to approve a payment or change a figure. Every consequential action stays on the page's own
   forms, under the page's own checks. tests/test_ui.py holds this file to that. */
(function () {
  const SLEEP_AFTER = 120000;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} },
    seen(k) { try { const s = sessionStorage.getItem(k); sessionStorage.setItem(k, "1"); return !!s; } catch (e) { return false; } },
  };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plain = o => JSON.stringify(o, (k, v) => (typeof v === "function" ? undefined : v));
  const sheet = () => matchMedia("(max-width:1023px)").matches; // Too narrow to sit beside the page, so it becomes a sheet.

  let dock, button, cat, panel, ctx = null, shown = "", chosen = null, sleepTimer = null, asleep = false, opener = null;
  const embedded = window.self !== window.top;

  function point(selector) {
    const el = typeof selector === "string" ? document.querySelector(selector) : selector;
    if (!el) return false;
    if (sheet()) close();
    el.scrollIntoView({ block: "center", behavior: Kitty.calm() ? "auto" : "smooth" });
    el.classList.remove("kc-point");
    void el.offsetWidth;
    el.classList.add("kc-point");
    setTimeout(() => el.classList.remove("kc-point"), 2400);
    return true;
  }

  function draw() {
    if (!ctx || !panel) return;
    const actions = ctx.actions || [];
    const picked = chosen !== null ? actions.find(a => a.label === chosen) : null;
    const facts = ctx.facts || [];
    const answer = picked ? picked.answer : "";
    const lines = answer ? (Array.isArray(answer) ? answer : [answer]).map(t => `<p>${esc(t)}</p>`).join("") : "";
    const next = picked && picked.then ? `<button class="btn small" data-next>${esc(picked.then.label)}</button>` : "";
    panel.querySelector(".kp-body").innerHTML = `
      <p class="kp-say">${esc(ctx.say)}</p>
      ${facts.length ? `<dl class="kp-facts">${facts.map(f => `<div><dt>${esc(f[0])}</dt><dd class="tabular">${esc(f[1])}</dd></div>`).join("")}</dl>` : ""}
      <div class="kp-answer" aria-live="polite">${lines ? `<h3>${esc(chosen)}</h3>${lines}${next}` : ""}</div>
      ${actions.length ? `<div class="kp-asks" role="group" aria-label="Ask Kitty">${actions.map((a, i) => `<button class="kp-ask" data-i="${i}" ${a.label === chosen ? 'aria-pressed="true"' : 'aria-pressed="false"'}>${esc(a.label)}</button>`).join("")}</div>` : ""}`;
    panel.querySelector("h2").textContent = ctx.title || "Kitty";
    const small = panel.querySelector("kitty-cat");
    if (small) small.setAttribute("mood", ctx.mood || "idle");
  }

  function wake() {
    if (asleep) { asleep = false; cat.rest((ctx && ctx.mood) || "idle"); }
    clearTimeout(sleepTimer);
    sleepTimer = setTimeout(() => {
      // Only a calm cat naps. If anything's waiting on someone, she stays awake with it.
      if (ctx && ctx.mood === "idle" && !panel.open && !document.hidden) { asleep = true; cat.rest("sleeping"); }
    }, SLEEP_AFTER);
  }

  function open() {
    if (panel.open) return;
    opener = document.activeElement;
    chosen = null;
    draw();
    if (sheet()) panel.showModal();
    else { panel.show(); document.documentElement.classList.add("kp-open"); } // The page moves over, so nothing ends up underneath.
    button.setAttribute("aria-expanded", "true");
    panel.querySelector(".kp-head h2").focus();
    wake();
  }
  function close() {
    if (!panel.open) return;
    panel.close();
    document.documentElement.classList.remove("kp-open");
    button.setAttribute("aria-expanded", "false");
    if (opener && opener.focus) opener.focus();
  }

  function hide(hidden) {
    store.set("kitty.cat.hidden", hidden ? "1" : null);
    dock.classList.toggle("kc-hidden", hidden);
    if (hidden) close();
  }

  function mount(opts = {}) {
    dock = document.querySelector(opts.dock || "#catdock");
    if (!dock) return;
    dock.classList.add("kc-dock");
    dock.innerHTML = `<button class="kc-btn" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="kittyPanel" aria-label="Ask Kitty"><kitty-cat mood="idle"></kitty-cat></button><button class="kc-show" type="button">Show Kitty</button>`;
    button = dock.querySelector(".kc-btn");
    cat = dock.querySelector("kitty-cat");
    if (embedded) { document.documentElement.classList.add("kc-embedded"); button.tabIndex = -1; button.setAttribute("aria-hidden", "true"); }
    panel = document.createElement("dialog");
    panel.id = "kittyPanel";
    panel.className = "kp";
    panel.setAttribute("aria-labelledby", "kittyPanelTitle");
    panel.innerHTML = `<div class="kp-head"><kitty-cat class="tiny" mood="idle"></kitty-cat><h2 id="kittyPanelTitle" tabindex="-1">Kitty</h2><button class="kp-x" type="button" aria-label="Close">${Kitty.icon("x", 20)}</button></div>
      <div class="kp-body"></div>
      <p class="kp-foot">I explain what this page shows. I can't approve a payment or change a figure. <button class="linkish" type="button" data-hide>Hide me</button></p>`;
    document.body.append(panel);
    button.addEventListener("click", () => (panel.open ? close() : open()));
    dock.querySelector(".kc-show").addEventListener("click", () => { hide(false); button.focus(); });
    panel.addEventListener("click", e => {
      if (e.target === panel) return close();
      if (e.target.closest(".kp-x")) return close();
      if (e.target.closest("[data-hide]")) { hide(true); dock.querySelector(".kc-show").focus(); return; }
      if (e.target.closest("[data-next]")) { act(ctx.actions.find(a => a.label === chosen).then); return; }
      const b = e.target.closest(".kp-ask");
      if (!b) return;
      const a = ctx.actions[+b.dataset.i];
      chosen = a.label;
      if (a.mood) cat.play(a.mood, 2600);
      draw();
      panel.querySelector(`.kp-ask[data-i="${b.dataset.i}"]`).focus();
      if (a.point || a.go) act(a);
    });
    panel.addEventListener("close", () => { document.documentElement.classList.remove("kp-open"); button.setAttribute("aria-expanded", "false"); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && panel.open) { e.preventDefault(); close(); } });
    ["pointerdown", "keydown", "scroll"].forEach(t => addEventListener(t, wake, { passive: true, capture: true }));
    hide(store.get("kitty.cat.hidden") === "1");
    wake();
  }

  function act(a) {
    if (a.point) point(a.point);
    if (a.go) { close(); a.go(); }
  }

  /* The page calls this whenever what it knows changes. */
  function set(next) {
    if (!panel) return;
    const was = ctx && ctx.id;
    ctx = next;
    if (next.id !== was) chosen = null;
    if (asleep && next.mood !== "idle") asleep = false;
    if (!asleep) cat.rest(next.mood || "idle");
    const key = plain(next) + "|" + chosen;
    if (key !== shown) { shown = key; draw(); }
    button.setAttribute("aria-label", `Ask Kitty: ${next.title || "help with this page"}`);
    // She says hello once on each screen: a wave, not a popup over what you came to read.
    if (next.hello && !store.seen("kitty.hello." + next.id)) cat.play(next.hello === true ? "greeting" : next.hello, 2600);
  }

  /* A moment the page has just seen happen for real. */
  function moment(mood, ms) { if (cat) { asleep = false; cat.play(mood, ms); } }

  window.Kitty = Object.assign(window.Kitty || {}, { companion: { mount, set, moment, point, open, close } });
})();
