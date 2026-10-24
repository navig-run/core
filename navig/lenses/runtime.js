/*
 * NAVIG Lens runtime — the ONE implementation every host runs.
 *
 * core/navig/lenses/compile.py wraps this file around each Lens:
 *
 *   (function () {
 *     <this file>
 *     navigLensRuntime(MANIFEST, HOST, function (GM_info, GM, GM_addStyle, …, navig) {
 *       <@require code> <the Lens source>
 *     });
 *   })();
 *
 * The Lens source is written into the output directly — never eval / new Function — so
 * Lenses run on sites with a strict Content-Security-Policy. Matching rules were compiled
 * to RegExp sources in Python (navig/lenses/patterns.py); this file only tests them.
 * Tampermonkey semantics: a Lens runs once per document, matched against the URL the
 * document loaded with. `navig.onUrlChange` is there for single-page apps.
 *
 * Parameter order of `body` is a contract with compile.py (_BODY_PARAMS) — keep in sync.
 */
function navigLensRuntime(m, host, body) {
  "use strict";
  var w = window;
  var RUNTIME_VERSION = "1";
  var tag = "[navig lens] " + (m && m.name ? m.name : "?");

  // ── Once per document per Lens (two hosts may inject the same Lens into one page) ──
  var seen = w.__navigLenses || (w.__navigLenses = {});
  if (seen[m.id]) return;

  // ── Frames + URL matching ──────────────────────────────────────────────────────────
  var inFrame = false;
  try { inFrame = w.top !== w; } catch (e) { inFrame = true; }
  if (m.noframes && inFrame) return;

  var url = String(location.href).split("#")[0];
  function test(list) {
    for (var i = 0; i < (list || []).length; i++) {
      try {
        if (new RegExp(list[i].source, list[i].flags || "").test(url)) return true;
      } catch (e) {
        console.warn(tag + ": unusable pattern /" + list[i].source + "/", e);
      }
    }
    return false;
  }
  if (!test(m.include) || test(m.exclude)) return;
  seen[m.id] = RUNTIME_VERSION;

  var granted = {};
  (m.grants || []).forEach(function (g) { granted[g] = true; });
  function has(name) {
    return !!(granted[name] || granted["GM." + name.replace(/^GM_/, "")] ||
      granted["GM_" + name.replace(/^GM\./, "")]);
  }

  // ── Host bridge (closure-only; see navig.capture below and docs/lenses.md) ──────────
  var bridge = typeof __navigBridge !== "undefined" ? __navigBridge : null; // eslint-disable-line no-undef

  // ── Storage ──────────────────────────────────────────────────────────────────────────
  //
  // Two backends, chosen by the host:
  //
  //  * Bridged (`__navigBridge.storage`, Chrome: the Lens's own USER_SCRIPT world). Values
  //    live in the extension, per Lens, and are PRELOADED before the Lens runs so
  //    GM_getValue stays synchronous. Page storage is never read or written: Web Storage is
  //    per origin, not per world, so anything put there — an AI-provider key — is readable
  //    by every script the page runs. Values a Lens left there before it was isolated are
  //    moved into the bridge on its next run and deleted from the page.
  //  * Unbridged (a host whose Lenses share the page's JavaScript world): per origin, JSON
  //    in localStorage (memory if unavailable). Readable by the page — such a host cannot
  //    keep a secret from the page anyway, since it also shares fetch, JSON and the DOM.
  var prefix = "navig-lens:" + m.id + ":";
  var mem = Object.create(null);
  var bstore = bridge && bridge.storage && typeof bridge.storage.load === "function" &&
    typeof bridge.storage.write === "function" ? bridge.storage : null;
  // Tells this document's own writes apart from another tab's when the host echoes changes.
  var client = Math.random().toString(36).slice(2) + Date.now().toString(36);
  var pending = Object.create(null); // key → writes not yet acknowledged by the host
  // Set when the bridged load fails: writing then would replace the user's real values
  // with whatever this page computed from defaults, so the page runs on memory only.
  var storageBroken = false;
  var ls = null;
  try { ls = w.localStorage; ls.getItem("navig-lens:probe"); } catch (e) { ls = null; }
  var pageLs = ls; // the bridged path reads it once, to migrate and scrub
  if (bstore) ls = null;
  function persist(key, raw) {
    if (!bstore || storageBroken) return;
    pending[key] = (pending[key] || 0) + 1;
    var settle = function () { if (!--pending[key]) delete pending[key]; };
    Promise.resolve()
      .then(function () { return bstore.write(key, raw, client); })
      .then(function (r) {
        settle();
        if (!r || r.ok !== true) console.warn(tag + ": value '" + key + "' not saved — " + ((r && r.error) || "no answer"));
      }, function (e) {
        settle();
        console.warn(tag + ": value '" + key + "' not saved —", e);
      });
  }
  function getValue(key, def) {
    var raw;
    try { raw = ls ? ls.getItem(prefix + key) : mem[key]; } catch (e) { raw = mem[key]; }
    if (raw === null || raw === undefined) return def;
    try { return JSON.parse(raw); } catch (e) { return def; }
  }
  function setValue(key, value) {
    var previous = getValue(key, undefined);
    var raw = JSON.stringify(value === undefined ? null : value);
    mem[key] = raw;
    try { if (ls) ls.setItem(prefix + key, raw); } catch (e) { /* quota / sandbox: memory only */ }
    persist(String(key), raw);
    fireListeners(key, previous, value, false);
  }
  function deleteValue(key) {
    var previous = getValue(key, undefined);
    delete mem[key];
    try { if (ls) ls.removeItem(prefix + key); } catch (e) { /* ignore */ }
    persist(String(key), null);
    fireListeners(key, previous, undefined, false);
  }
  function listValues() {
    var out = Object.keys(mem);
    try {
      if (ls) {
        for (var i = 0; i < ls.length; i++) {
          var k = ls.key(i);
          if (k && k.indexOf(prefix) === 0 && out.indexOf(k.slice(prefix.length)) < 0) {
            out.push(k.slice(prefix.length));
          }
        }
      }
    } catch (e) { /* ignore */ }
    return out;
  }

  // ── GM_addValueChangeListener ───────────────────────────────────────────────────────
  //
  // Storage is per-origin localStorage, and the browser already mirrors that between the
  // tabs of an origin: the `storage` event fires in every OTHER document on a change.
  // That is exactly the cross-tab signal Tampermonkey gives, so a Lens that merges
  // concurrent edits can see a tab arrive under it instead of overwriting it.
  //
  // `remote` is false for our own writes (fired directly) and true for a write from
  // another tab (fired off the storage event) — the distinction scripts use to skip
  // re-merging their own change.
  var listeners = [];
  var listenerSeq = 0;
  var storageHook = null;

  function rawToValue(raw) {
    if (raw === null || raw === undefined) return undefined;
    try { return JSON.parse(raw); } catch (e) { return undefined; }
  }

  function fireListeners(key, oldValue, newValue, remote) {
    // Copy first: a listener may unsubscribe (or subscribe) while we notify.
    var pending = listeners.slice();
    for (var i = 0; i < pending.length; i++) {
      if (pending[i].key !== key) continue;
      try { pending[i].fn(key, oldValue, newValue, remote); }
      catch (e) { console.error(tag + ":", e); }
    }
  }

  function addValueChangeListener(name, fn) {
    if (typeof fn !== "function") return undefined;
    var key = String(name);
    var id = ++listenerSeq;
    listeners.push({ id: id, key: key, fn: fn });
    // Bridged: changes come from the host, never from page `storage` events — the page can
    // dispatch a forged StorageEvent that a listener in any world would receive.
    if (bstore) {
      startWatch();
    } else if (!storageHook && w.addEventListener) {
      storageHook = function (ev) {
        if (!ev || ev.key === null) return;               // storage.clear() — not attributable
        if (ev.key.indexOf(prefix) !== 0) return;         // another Lens's keys
        fireListeners(ev.key.slice(prefix.length), rawToValue(ev.oldValue), rawToValue(ev.newValue), true);
      };
      w.addEventListener("storage", storageHook);
    }
    return id;
  }

  function removeValueChangeListener(id) {
    for (var i = 0; i < listeners.length; i++) {
      if (listeners[i].id === id) {
        listeners.splice(i, 1);
        if (!listeners.length && storageHook && w.removeEventListener) {
          w.removeEventListener("storage", storageHook);
          storageHook = null;
        }
        if (!listeners.length && watching) {
          var gone = watching;
          watching = null; // first, so the close below does not schedule a reconnect
          try { gone.close(); } catch (e) { /* ignore */ }
        }
        return true;
      }
    }
    return false;
  }

  // ── Bridged storage: preload, migrate out of the page, and other tabs' changes ───────
  //
  // The bridged equivalent of the `storage` event: the host pushes every change another
  // document of this Lens made (`{key, raw}`), and a full `{values}` snapshot on each
  // (re)connect so a gap — the extension's worker restarting — is reported as changes
  // rather than lost. A key with a write of ours still in flight is skipped: the host has
  // not seen it yet, so its snapshot is older than what this page holds.
  var watching = null;
  function startWatch() {
    if (!bstore || typeof bstore.watch !== "function" || watching || storageBroken) return;
    try {
      watching = bstore.watch(client, onRemote, function () {
        watching = null;
        if (listeners.length) setTimeout(startWatch, 1000);
      }) || null;
    } catch (e) {
      watching = null;
      console.warn(tag + ": other tabs' changes unavailable —", e);
    }
  }
  function applyRemote(key, raw) {
    if (pending[key]) return;
    var before = mem[key];
    if (before === (raw === null ? undefined : raw)) return;
    if (raw === null) delete mem[key]; else mem[key] = raw;
    fireListeners(key, rawToValue(before), rawToValue(raw), true);
  }
  function onRemote(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.values && typeof msg.values === "object") {
      var inSnapshot = Object.create(null);
      Object.keys(msg.values).forEach(function (k) {
        inSnapshot[k] = true;
        applyRemote(k, typeof msg.values[k] === "string" ? msg.values[k] : null);
      });
      Object.keys(mem).forEach(function (k) { if (!inSnapshot[k]) applyRemote(k, null); });
      return;
    }
    if (typeof msg.key === "string") applyRemote(msg.key, typeof msg.raw === "string" ? msg.raw : null);
  }
  function migrateFromPage() {
    if (!pageLs) return;
    var found = [];
    try {
      for (var i = 0; i < pageLs.length; i++) {
        var k = pageLs.key(i);
        if (k && k.indexOf(prefix) === 0) found.push(k);
      }
    } catch (e) { return; }
    found.forEach(function (full) {
      var key = full.slice(prefix.length);
      var raw = null;
      try { raw = pageLs.getItem(full); } catch (e) { return; }
      if (raw === null) return;
      var scrub = function () { try { pageLs.removeItem(full); } catch (e) { /* ignore */ } };
      if (key in mem) { scrub(); return; } // the protected copy is the newer truth
      mem[key] = raw;
      pending[key] = (pending[key] || 0) + 1;
      Promise.resolve()
        .then(function () { return bstore.write(key, raw, client); })
        .then(function (r) {
          if (!--pending[key]) delete pending[key];
          // Scrub only once the protected copy exists — never lose the user's value.
          if (r && r.ok === true) scrub();
          else console.warn(tag + ": could not move '" + key + "' out of page storage — " + ((r && r.error) || "no answer"));
        }, function (e) {
          if (!--pending[key]) delete pending[key];
          console.warn(tag + ": could not move '" + key + "' out of page storage —", e);
        });
    });
  }
  var storageReady = null; // null = nothing to wait for (unbridged)
  if (bstore) {
    storageReady = Promise.resolve()
      .then(function () { return bstore.load(); })
      .then(function (r) {
        var values = r && r.ok !== false && r.values && typeof r.values === "object" ? r.values : null;
        if (!values) throw new Error((r && r.error) || "no answer");
        Object.keys(values).forEach(function (k) {
          if (typeof values[k] === "string") mem[k] = values[k];
        });
        migrateFromPage();
      })
      .catch(function (e) {
        storageBroken = true;
        console.warn(tag + ": stored values unavailable, this page keeps changes in memory only —", e);
      });
  }

  // ── DOM helpers ─────────────────────────────────────────────────────────────────────
  function whenRoot(fn) {
    if (document.documentElement) return fn();
    var mo = new MutationObserver(function () {
      if (document.documentElement) { mo.disconnect(); fn(); }
    });
    mo.observe(document, { childList: true });
  }
  function addStyle(css) {
    var el = document.createElement("style");
    el.setAttribute("data-navig-lens", m.id);
    el.textContent = String(css);
    whenRoot(function () { (document.head || document.documentElement).appendChild(el); });
    // A strict `style-src` CSP blocks an inline <style>; a constructable stylesheet is not
    // subject to it. Add both (identical rules are harmless) and keep the Tampermonkey
    // contract — the return value is the <style> element, and removing it removes both.
    try {
      if (w.CSSStyleSheet && "adoptedStyleSheets" in document) {
        var sheet = new CSSStyleSheet();
        sheet.replaceSync(String(css));
        document.adoptedStyleSheets = document.adoptedStyleSheets.concat([sheet]);
        var removeEl = el.remove.bind(el);
        el.remove = function () {
          removeEl();
          document.adoptedStyleSheets = document.adoptedStyleSheets.filter(function (s) {
            return s !== sheet;
          });
        };
      }
    } catch (e) { /* older engine: the <style> element is all there is */ }
    return el;
  }

  // A tiny shared overlay (toasts + a launcher for buttons / menu commands), in a shadow
  // root so page CSS can't restyle it and Lens CSS can't leak into it. OPEN, not closed:
  // in the page's own world "closed" protects nothing (the page can patch attachShadow),
  // and open keeps the menu reachable to assistive tech and to an agent driving the page.
  function ui() {
    if (w.__navigLensUI) return w.__navigLensUI;
    var state = { items: [], root: null, menu: null, pill: null, toasts: null };
    function mount() {
      if (state.root || !document.body) return !!state.root;
      var hostEl = document.createElement("navig-lens-ui");
      hostEl.style.cssText = "all:initial;position:fixed;z-index:2147483646;right:16px;bottom:16px;";
      var root = hostEl.attachShadow({ mode: "open" });
      var css =
        ":host{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#e6e6e6}" +
        ".t{display:flex;flex-direction:column;gap:6px;align-items:flex-end;margin-bottom:8px}" +
        ".toast{background:#111;border:1px solid #333;border-radius:6px;padding:6px 10px;max-width:320px}" +
        ".toast.error{border-color:#a33}.toast.success{border-color:#2a7}" +
        ".pill{display:none;margin-left:auto;background:#111;border:1px solid #333;border-radius:999px;" +
        "width:28px;height:28px;color:#e6e6e6;cursor:pointer;font:inherit}" +
        ".pill:focus-visible,.menu button:focus-visible{outline:2px solid #6af;outline-offset:2px}" +
        ".menu{display:none;flex-direction:column;background:#111;border:1px solid #333;border-radius:6px;" +
        "margin-bottom:6px;padding:4px;min-width:180px}" +
        ".menu button{all:unset;padding:6px 8px;border-radius:4px;cursor:pointer}" +
        ".menu button:hover{background:#222}" +
        "@media (prefers-reduced-motion:no-preference){.toast{transition:opacity .2s}}";
      // DOM APIs, never innerHTML: Trusted-Types sites throw on any string assignment.
      function el(tag, cls) {
        var e = document.createElement(tag);
        e.className = cls;
        root.appendChild(e);
        return e;
      }
      state.toasts = el("div", "t");
      state.menu = el("div", "menu");
      state.menu.setAttribute("role", "menu");
      state.pill = el("button", "pill");
      state.pill.textContent = "N";
      state.pill.setAttribute("aria-label", "NAVIG Lens actions");
      state.pill.title = "NAVIG Lens actions";
      state.pill.setAttribute("aria-haspopup", "menu");
      state.pill.setAttribute("aria-expanded", "false");
      // Constructable sheet first: an inline <style> is blocked by a strict style-src CSP.
      try {
        var sheet = new CSSStyleSheet();
        sheet.replaceSync(css);
        root.adoptedStyleSheets = [sheet];
      } catch (e) {
        var st = document.createElement("style");
        st.textContent = css;
        root.appendChild(st);
      }
      state.setOpen = function (open) {
        state.menu.style.display = open ? "flex" : "none";
        state.pill.setAttribute("aria-expanded", open ? "true" : "false");
      };
      state.pill.addEventListener("click", function () {
        state.setOpen(state.menu.style.display !== "flex");
      });
      root.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { state.setOpen(false); state.pill.focus(); }
      });
      document.body.appendChild(hostEl);
      state.root = root;
      render();
      return true;
    }
    function render() {
      if (!state.root) return;
      while (state.menu.firstChild) state.menu.removeChild(state.menu.firstChild);
      state.items.forEach(function (it) {
        var b = document.createElement("button");
        b.setAttribute("role", "menuitem");
        b.textContent = (it.icon ? it.icon + "  " : "") + it.title;
        b.addEventListener("click", function () {
          state.setOpen(false);
          try { it.onClick(); } catch (e) { console.error("[navig lens] " + it.lens + ":", e); }
        });
        state.menu.appendChild(b);
      });
      state.pill.style.display = state.items.length ? "block" : "none";
      if (!state.items.length) state.setOpen(false);
    }
    function ready(fn) {
      if (mount()) return fn();
      document.addEventListener("DOMContentLoaded", function () { mount(); fn(); }, { once: true });
    }
    state.add = function (item) {
      // A host bar (the Chrome extension's dock) may render buttons itself: it marks the
      // page and listens for this event. If it takes the item, the overlay stays hidden.
      var ev = new CustomEvent("navig-lens:button", { detail: item, cancelable: true });
      if (document.documentElement && document.documentElement.hasAttribute("data-navig-lens-host") &&
          !document.dispatchEvent(ev)) {
        return function () {
          document.dispatchEvent(new CustomEvent("navig-lens:button-remove", { detail: item }));
        };
      }
      state.items.push(item);
      ready(render);
      return function () {
        var i = state.items.indexOf(item);
        if (i >= 0) state.items.splice(i, 1);
        render();
      };
    };
    state.toast = function (text, kind) {
      ready(function () {
        var t = document.createElement("div");
        t.className = "toast " + (kind || "");
        t.setAttribute("role", kind === "error" ? "alert" : "status");
        t.textContent = String(text);
        state.toasts.appendChild(t);
        setTimeout(function () { t.remove(); }, kind === "error" ? 6000 : 3000);
      });
    };
    w.__navigLensUI = state;
    return state;
  }

  function addButton(spec) {
    var s = spec || {};
    return ui().add({
      lens: m.name,
      title: String(s.title || m.name),
      icon: s.icon ? String(s.icon) : "",
      onClick: typeof s.onClick === "function" ? s.onClick : function () {},
    });
  }

  // ── SPA navigation (shared single patch of history) ────────────────────────────────
  function onUrlChange(cb) {
    var bus = w.__navigLensNav;
    if (!bus) {
      bus = w.__navigLensNav = { subs: [], last: location.href };
      var fire = function () {
        if (location.href === bus.last) return;
        bus.last = location.href;
        bus.subs.slice().forEach(function (fn) {
          try { fn(location.href); } catch (e) { console.error("[navig lens] onUrlChange:", e); }
        });
      };
      ["pushState", "replaceState"].forEach(function (name) {
        var orig = history[name];
        history[name] = function () {
          var r = orig.apply(this, arguments);
          fire();
          return r;
        };
      });
      w.addEventListener("popstate", fire);
      // In an isolated world the patch above sees only this world's own pushState calls,
      // not the page's. The Navigation API event is dispatched to every world.
      try {
        if (w.navigation && typeof w.navigation.addEventListener === "function") {
          w.navigation.addEventListener("currententrychange", fire);
        }
      } catch (e) { /* no Navigation API: the history patch is all there is */ }
      w.addEventListener("hashchange", fire);
    }
    bus.subs.push(cb);
    return function () {
      var i = bus.subs.indexOf(cb);
      if (i >= 0) bus.subs.splice(i, 1);
    };
  }

  // ── GM API ──────────────────────────────────────────────────────────────────────────
  var info = {
    script: {
      name: m.name, namespace: m.namespace, version: m.version, description: m.description,
      matches: m.matches, includes: m.includes, excludes: m.excludes,
      grant: m.grants, "run-at": m.runAt, noframes: m.noframes,
    },
    scriptHandler: "NAVIG",
    version: RUNTIME_VERSION,
    navig: { host: host, lensId: m.id },
  };
  function openInTab(u) { return w.open(String(u), "_blank"); }
  function setClipboard(text) {
    try { return navigator.clipboard.writeText(String(text)); } catch (e) { return Promise.reject(e); }
  }
  function notification(a, b) {
    var text = typeof a === "object" && a ? a.text || a.title : a;
    ui().toast(b ? b + ": " + text : text);
  }
  function registerMenuCommand(caption, fn) {
    return addButton({ title: caption, onClick: fn });
  }
  function log() {
    var args = Array.prototype.slice.call(arguments);
    console.log.apply(console, [tag + ":"].concat(args));
  }

  // ── GM_xmlhttpRequest ───────────────────────────────────────────────────────────────
  //
  // A page-context fetch — the honest ceiling for a script that shares the page's
  // JavaScript world. Same-origin and CORS-permitted requests work; anything the page
  // itself could not fetch (`file://`, or a host that does not allow this origin) fails
  // through `onerror` carrying the reason, so a feature-detecting script reports it
  // instead of hanging.
  //
  // Deliberately NOT proxied through the extension or the daemon: a call made from page
  // JavaScript can be made by ANY script on that page, so a privileged proxy would hand
  // every web page a reader for the local daemon's loopback API (unauthenticated for
  // local clients) and for local files. Serving those needs an operator-decided
  // allowlist, not an open channel — see docs/lenses.md.
  function xmlHttpRequest(d) {
    var detail = d || {};
    var url = String(detail.url || "");
    var method = String(detail.method || "GET").toUpperCase();
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = null;
    var settled = false;
    var timedOut = false;

    var handle = {
      abort: function () {
        if (settled) return;
        settled = true;
        if (timer) { try { clearTimeout(timer); } catch (e) { /* ignore */ } }
        if (controller) { try { controller.abort(); } catch (e) { /* ignore */ } }
      },
    };

    function call(name, arg) {
      if (typeof detail[name] !== "function") return;
      try { detail[name](arg); } catch (e) { console.error(tag + ":", e); }
    }

    function finish(payload, ok) {
      if (settled) return;
      settled = true;
      if (timer) { try { clearTimeout(timer); } catch (e) { /* ignore */ } }
      if (ok) { call("onload", payload); }
      else if (timedOut) { call("ontimeout", payload); }
      else { call("onerror", payload); }
      call("onloadend", payload);
    }

    if (typeof fetch !== "function" || !url) {
      finish({ status: 0, statusText: "", responseText: "", finalUrl: url, error: "fetch unavailable" }, false);
      return handle;
    }

    if (detail.timeout > 0) {
      timer = setTimeout(function () {
        if (settled) return;
        timedOut = true;
        if (controller) { try { controller.abort(); } catch (e) { /* ignore */ } }
        finish({ status: 0, statusText: "", responseText: "", finalUrl: url, error: "timeout" }, false);
      }, Number(detail.timeout));
    }

    var headers;
    try {
      headers = new Headers(detail.headers || {});
    } catch (e) {
      headers = null;
    }
    var init = { method: method, headers: headers || undefined, signal: controller ? controller.signal : undefined };
    if (detail.data !== undefined && detail.data !== null && method !== "GET" && method !== "HEAD") {
      init.body = detail.data;
    }

    fetch(url, init).then(function (res) {
      if (settled) return;
      var headerText = "";
      try {
        res.headers.forEach(function (v, k) { headerText += k + ": " + v + "\r\n"; });
      } catch (e) { /* ignore */ }
      var want = detail.responseType === "json" ? res.json() : res.text();
      return want.then(function (body) {
        finish({
          readyState: 4,
          status: res.status,
          statusText: res.statusText,
          responseText: typeof body === "string" ? body : JSON.stringify(body === undefined ? null : body),
          response: body,
          responseHeaders: headerText,
          finalUrl: res.url || url,
        }, true);
      }, function () {
        // A body we cannot decode still has a status worth reporting.
        finish({ readyState: 4, status: res.status, statusText: res.statusText, responseText: "", response: null, responseHeaders: headerText, finalUrl: res.url || url }, true);
      });
    }).catch(function (err) {
      finish({
        status: 0, statusText: "", responseText: "", finalUrl: url,
        error: String((err && err.message) || err || "request failed"),
        // The reason a script needs: a CORS block and a blocked scheme both arrive here.
        errorText: String((err && err.message) || err || "request failed"),
      }, false);
    });
    return handle;
  }

  function pick(name, fn) { return has(name) ? fn : undefined; }
  var resolve = function (v) { return Promise.resolve(v); };
  var GM = {
    info: info,
    addStyle: pick("GM_addStyle", function (c) { return resolve(addStyle(c)); }),
    getValue: pick("GM_getValue", function (k, d) { return resolve(getValue(k, d)); }),
    setValue: pick("GM_setValue", function (k, v) { return resolve(setValue(k, v)); }),
    deleteValue: pick("GM_deleteValue", function (k) { return resolve(deleteValue(k)); }),
    listValues: pick("GM_listValues", function () { return resolve(listValues()); }),
    openInTab: pick("GM_openInTab", function (u) { return resolve(openInTab(u)); }),
    setClipboard: pick("GM_setClipboard", setClipboard),
    notification: pick("GM_notification", function (a, b) { return resolve(notification(a, b)); }),
    registerMenuCommand: pick("GM_registerMenuCommand", function (c, f) { return resolve(registerMenuCommand(c, f)); }),
    xmlHttpRequest: pick("GM_xmlhttpRequest", xmlHttpRequest),
    addValueChangeListener: pick("GM_addValueChangeListener", addValueChangeListener),
    removeValueChangeListener: pick("GM_removeValueChangeListener", removeValueChangeListener),
  };

  var navig = {
    host: host,
    lensId: m.id,
    toast: function (text, kind) { ui().toast(text, kind); },
    addButton: addButton,
    onUrlChange: onUrlChange,
    storage: {
      get: function (k, d) { return resolve(getValue(k, d)); },
      set: function (k, v) { return resolve(setValue(k, v)); },
      remove: function (k) { return resolve(deleteValue(k)); },
    },
  };

  // ── navig.capture — privileged, so only through a channel page JavaScript cannot reach ──
  // A host that can prove which Lens is calling wraps the compiled script in a closure
  // holding `__navigBridge` (Chrome: an isolated USER_SCRIPT world + a per-Lens token). It
  // is never a global. Without the grant, or on a host with no bridge, `navig.capture` is
  // simply absent — `typeof navig.capture === "function"` is the feature test. (`bridge`
  // is read at the top, with storage; a storage-only bridge has no `send`.)
  if (granted["navig.capture"] && bridge && typeof bridge.send === "function") {
    /**
     * Send text to the NAVIG inbox. `what` is a string, or {content, title?, url?, kind?, note?}
     * (kind: "selection" | "page" | "link"; default "selection"). Resolves {ok, queued?, error?};
     * never rejects. When NAVIG is not running the capture waits and is sent later.
     */
    navig.capture = function (what) {
      var o = typeof what === "string" ? { content: what } : (what || {});
      var content = String(o.content == null ? "" : o.content);
      if (!content.trim()) return Promise.resolve({ ok: false, error: "nothing to capture" });
      var kind = o.kind === "page" || o.kind === "link" ? o.kind : "selection";
      var msg = {
        kind: kind,
        content: content.slice(0, 200000),
        title: String(o.title || document.title || "").slice(0, 300),
        url: String(o.url || location.href),
        note: o.note ? String(o.note).slice(0, 2000) : undefined,
      };
      return Promise.resolve()
        .then(function () { return bridge.send(msg); })
        .then(function (r) { return r && typeof r === "object" ? r : { ok: false, error: "no answer" }; })
        .catch(function (e) { return { ok: false, error: String((e && e.message) || e) }; });
    };
  }

  function run() {
    try {
      body.call(
        w,
        info,
        GM,
        pick("GM_addStyle", addStyle),
        pick("GM_getValue", getValue),
        pick("GM_setValue", setValue),
        pick("GM_deleteValue", deleteValue),
        pick("GM_listValues", listValues),
        pick("GM_openInTab", openInTab),
        pick("GM_setClipboard", setClipboard),
        pick("GM_notification", notification),
        pick("GM_registerMenuCommand", registerMenuCommand),
        log,
        w,
        navig,
        pick("GM_xmlhttpRequest", xmlHttpRequest),
        pick("GM_addValueChangeListener", addValueChangeListener),
        pick("GM_removeValueChangeListener", removeValueChangeListener)
      );
    } catch (e) {
      console.error(tag + ":", e);
    }
  }

  // ── @run-at ─────────────────────────────────────────────────────────────────────────
  //
  // A bridged Lens starts once its values have arrived (GM_getValue is synchronous), so
  // its document-start is one message round trip after the document starts. Unbridged
  // Lenses are scheduled synchronously, exactly as before.
  function schedule() {
    var when = m.runAt || "document-idle";
    if (when === "document-start") {
      run();
    } else if (when === "document-body") {
      if (document.body) run();
      else {
        var mo = new MutationObserver(function () {
          if (document.body) { mo.disconnect(); run(); }
        });
        mo.observe(document.documentElement || document, { childList: true, subtree: true });
      }
    } else if (document.readyState !== "loading") {
      if (when === "document-end") run();
      else setTimeout(run, 0);
    } else {
      document.addEventListener("DOMContentLoaded", function () {
        if (when === "document-end") run();
        else setTimeout(run, 0);
      }, { once: true });
    }
  }
  if (storageReady) storageReady.then(schedule);
  else schedule();
}
