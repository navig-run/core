"""Assets for the LAN join flow — the /install page and the two join scripts.

Kept apart from the route module so each asset reads top-to-bottom as what it
is. Values are substituted with ``render()`` (plain ``{{name}}`` placeholders),
never ``str.format`` — the page carries CSS and the scripts carry PowerShell /
bash braces that ``format`` would choke on.

Design (page): the NAVIG OS web connect screen (`apps/os/apps/webui/src/hosted/
connect-screen.css`) — flat-hex tokens, azure top glow, a fading hairline grid,
mono letter-spaced labels, 4 px radii, the inline NAVIG mark. No external
requests at all: system font stack, inline SVG mark, server-rendered QR.

Scripts: both delegate the install itself to the OFFICIAL installers
(navig.run/install.ps1 · install.sh), which bring their own Python runtime and
fix PATH, with the ``server`` profile — daemon + gateway, no public tunnel
(``cloud.enabled: false``). A device joining a LAN must never publish itself
on the internet; the default ``full`` profile would.
"""

from __future__ import annotations

from html import escape


def render(template: str, **values: str) -> str:
    """Replace ``{{key}}`` placeholders; every key must be used exactly as given."""
    out = template
    for key, value in values.items():
        token = "{{" + key + "}}"
        if token not in out:
            raise KeyError(f"template has no placeholder {token}")
        out = out.replace(token, value)
    return out


# ─────────────────────────── the NAVIG mark ──────────────────────────

NAVIG_MARK_SVG = (
    '<svg class="mark" xmlns="http://www.w3.org/2000/svg" viewBox="192 128 640 768" '
    'aria-hidden="true"><defs><linearGradient id="nvg" x1="0%" y1="100%" x2="100%" y2="0%">'
    '<stop offset="0%" stop-color="#2A6BFF"/><stop offset="55%" stop-color="#12C8FF"/>'
    '<stop offset="100%" stop-color="#17FFF0"/></linearGradient></defs>'
    '<g fill="url(#nvg)" fill-rule="evenodd">'
    '<path d="M 512 144 L 349 303 L 414 369 L 384 457 L 409 479 L 392 517 L 415 556 L 362 612 '
    "L 297 566 L 338 426 L 326 358 L 258 291 L 268 396 L 298 427 L 232 548 L 274 621 L 236 662 "
    "L 236 728 L 336 814 L 297 682 L 400 680 L 395 782 L 491 883 L 492 787 L 456 732 L 511 635 "
    "L 567 732 L 530 786 L 531 882 L 627 783 L 622 680 L 727 682 L 685 814 L 788 726 L 788 663 "
    "L 750 619 L 790 547 L 724 428 L 755 395 L 766 291 L 696 358 L 683 430 L 725 566 L 663 612 "
    'L 607 557 L 630 518 L 614 478 L 639 456 L 609 370 L 673 303 Z"/>'
    '<path d="M 500 182 L 502 184 L 501 533 L 405 449 L 437 361 L 493 192 Z"/>'
    '<path d="M 521 180 L 532 197 L 584 358 L 617 447 L 613 453 L 521 532 Z"/>'
    "</g></svg>"
)


# ─────────────────────────── the page ────────────────────────────────

PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<meta name="robots" content="noindex">
<title>Add a device · NAVIG</title>
<style>
:root{
  --bg:#050505; --fg:#f5f5f5; --muted:#a1a1aa; --dim:#71717a;
  --panel:#0a0a0a; --raised:#111113; --input:#141414;
  --border:#262626; --hairline:#1a1a1a;
  --primary:#2c8bb7; --accent:#22d3ee; --accent-ink:#071017; --ok:#4ade80;
  --glow:rgba(44,139,183,.16); --grid:rgba(255,255,255,.035);
  --sans:"Geist Variable","Geist",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Geist Mono Variable","Geist Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --r:4px;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#f3f4f6; --fg:#18191c; --muted:#5b5d66; --dim:#7a7c85;
    --panel:#ffffff; --raised:#f8f9fa; --input:#eef0f2;
    --border:#dcdfe3; --hairline:#e7e9ec;
    --primary:#1b6489; --accent:#155e75; --accent-ink:#ffffff; --ok:#15803d;
    --glow:rgba(27,100,137,.12); --grid:rgba(0,0,0,.04);
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{min-height:100%}
body{font:15px/1.6 var(--sans);color:var(--fg);
  background:radial-gradient(ellipse 70% 45% at 50% -10%,var(--glow),transparent 70%),var(--bg);
  -webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:linear-gradient(to right,var(--grid) 1px,transparent 1px),
                   linear-gradient(to bottom,var(--grid) 1px,transparent 1px);
  background-size:48px 48px;
  -webkit-mask-image:radial-gradient(ellipse 60% 55% at 50% 30%,#000,transparent 80%);
          mask-image:radial-gradient(ellipse 60% 55% at 50% 30%,#000,transparent 80%)}
.top{position:relative;z-index:1;display:flex;align-items:center;gap:10px;
  padding:14px 24px;border-bottom:1px solid var(--hairline)}
.top .mark{width:20px;height:24px}
.brand{font:600 13px var(--mono);letter-spacing:.18em}
.top .where{margin-left:auto;font:12px var(--mono);color:var(--dim)}
main{position:relative;z-index:1;width:100%;max-width:760px;margin:0 auto;padding:56px 24px 40px}
.hero{text-align:center;margin-bottom:36px;animation:rise .8s cubic-bezier(.16,1,.3,1) both}
.hero .mark{display:block;margin:0 auto;width:64px;height:77px;
  filter:drop-shadow(0 8px 24px rgba(44,139,183,.35));animation:breathe 2.4s ease-in-out infinite}
.kicker{display:flex;justify-content:center;align-items:center;gap:8px;margin:22px 0 12px;
  font:11px var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.kicker::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--accent);
  box-shadow:0 0 10px var(--accent)}
h1{font-size:clamp(28px,6vw,38px);font-weight:600;letter-spacing:-.02em;line-height:1.1}
.lead{max-width:540px;margin:14px auto 0;color:var(--muted)}
.lead b{color:var(--fg);font-weight:600}
.chips{display:flex;justify-content:center;flex-wrap:wrap;gap:8px;margin-top:20px}
.chip{font:12px var(--mono);color:var(--muted);border:1px solid var(--border);
  border-radius:999px;padding:3px 10px;background:color-mix(in srgb,var(--panel) 80%,transparent)}
.chip i{font-style:normal;color:var(--ok)}
.card{background:color-mix(in srgb,var(--panel) 92%,transparent);border:1px solid var(--border);
  border-radius:var(--r);padding:24px;margin-bottom:20px;
  box-shadow:0 18px 50px rgba(0,0,0,.35),0 0 0 1px rgba(34,211,238,.04);
  animation:rise .8s .08s cubic-bezier(.16,1,.3,1) both}
.label{font:11px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
.tabs{display:flex;gap:4px;padding:3px;margin:10px 0 16px;border:1px solid var(--border);
  border-radius:var(--r);background:var(--input);width:max-content;max-width:100%}
.tab{font:500 13px var(--sans);color:var(--muted);background:none;border:0;border-radius:3px;
  padding:7px 14px;cursor:pointer}
.tab:hover{color:var(--fg)}
.tab[aria-selected="true"]{background:var(--raised);color:var(--fg);
  box-shadow:0 0 0 1px var(--border)}
.tab:focus-visible,.copy:focus-visible{outline:none;box-shadow:0 0 0 3px rgba(34,211,238,.25)}
.panel[hidden]{display:none}
.where-run{color:var(--muted);font-size:14px;margin-bottom:10px}
.where-run b{color:var(--fg);font-weight:600}
.cmd{display:flex;align-items:stretch;border:1px solid var(--border);border-radius:var(--r);
  background:var(--input);overflow:hidden}
.cmd code{flex:1;min-width:0;padding:14px 16px;font:13.5px/1.5 var(--mono);color:var(--fg);
  white-space:nowrap;overflow-x:auto;user-select:all;scrollbar-width:thin}
.cmd code::before{content:"$ ";color:var(--primary);user-select:none}
.copy{flex:none;min-width:104px;display:inline-flex;align-items:center;justify-content:center;gap:6px;
  font:600 13px var(--sans);color:var(--accent-ink);background:var(--accent);border:0;
  border-left:1px solid var(--accent);cursor:pointer;transition:filter .15s,box-shadow .15s}
.copy:hover{filter:brightness(1.08);box-shadow:0 0 24px rgba(34,211,238,.25)}
.copy.done{background:var(--ok);border-color:var(--ok)}
.hint{margin-top:10px;font-size:13px;color:var(--dim)}
.hint kbd{font:12px var(--mono);border:1px solid var(--border);border-bottom-width:2px;
  border-radius:3px;padding:0 5px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:1fr 220px;gap:20px;align-items:stretch}
.steps{list-style:none;counter-reset:s;display:grid;gap:14px}
.steps li{counter-increment:s;display:grid;grid-template-columns:28px 1fr;gap:12px;align-items:start}
.steps li::before{content:counter(s);display:grid;place-items:center;width:28px;height:28px;
  font:600 12px var(--mono);color:var(--accent);border:1px solid var(--border);border-radius:var(--r);
  background:var(--input)}
.steps b{display:block;font-weight:600}
.steps span{color:var(--muted);font-size:14px}
.qr{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;text-align:center}
.qr .code{width:168px;height:168px;padding:8px;background:#fff;border-radius:var(--r);
  box-shadow:0 0 0 1px var(--border),0 10px 30px rgba(44,139,183,.18)}
.qr .code svg{display:block;width:100%;height:100%}
.qr p{font-size:12px;color:var(--dim);max-width:190px}
.qr .url{font:12px var(--mono);color:var(--accent);word-break:break-all}
.feats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.feat{border:1px solid var(--hairline);border-radius:var(--r);padding:14px;background:var(--raised)}
.feat b{display:block;font-size:14px;font-weight:600;margin-bottom:4px}
.feat span{font-size:13px;color:var(--muted)}
.after{display:grid;gap:8px;margin-top:4px}
.after div{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;font-size:14px;color:var(--muted)}
.after code{font:13px var(--mono);color:var(--fg);background:var(--input);border:1px solid var(--hairline);
  border-radius:3px;padding:1px 7px}
.mobile-note{display:none;margin-bottom:20px;border:1px solid rgba(250,204,21,.35);
  background:rgba(250,204,21,.06);color:var(--fg);border-radius:var(--r);padding:12px 14px;font-size:14px}
body.is-mobile .mobile-note{display:block}
footer{position:relative;z-index:1;max-width:760px;margin:8px auto 0;padding:18px 24px 40px;
  border-top:1px solid var(--hairline);display:flex;flex-wrap:wrap;gap:6px 18px;
  font:12px var(--mono);color:var(--dim)}
footer .lock{color:var(--ok)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes breathe{0%,100%{transform:scale(1);opacity:.92}50%{transform:scale(1.05);opacity:1}}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
@media (max-width:680px){
  main{padding:36px 16px 28px}
  .grid2{grid-template-columns:1fr}
  .feats{grid-template-columns:1fr}
  .top .where{display:none}
  .cmd{flex-direction:column}
  .copy{min-height:44px;border-left:0;border-top:1px solid var(--accent)}
}
</style>
</head>
<body>
<header class="top">{{mark}}<span class="brand">NAVIG</span>
  <span class="where">{{hub_name}} · {{gateway_host}}</span></header>
<main>
  <section class="hero">
    {{mark}}
    <div class="kicker">Add a device</div>
    <h1>Bring this computer into your NAVIG</h1>
    <p class="lead">One command installs NAVIG on the computer you are using right now and links
      it to <b>{{hub_name}}</b>. It runs quietly in the background — no accounts, nothing leaves
      your network.</p>
    <div class="chips">
      <span class="chip"><i>●</i> {{hub_name}} is online</span>
      <span class="chip">{{peer_summary}}</span>
      <span class="chip">LAN only</span>
    </div>
  </section>

  <div class="mobile-note">You are on a phone. Open <b>{{page_url}}</b> on the
    <b>computer</b> you want to add, and run the command there.</div>

  <section class="card" aria-labelledby="run-h">
    <div class="label" id="run-h">1 · Run one command</div>
    <div class="tabs" role="tablist" aria-label="Operating system">
      <button class="tab" role="tab" id="t-win" aria-controls="p-win" aria-selected="true">Windows</button>
      <button class="tab" role="tab" id="t-mac" aria-controls="p-mac" aria-selected="false">macOS</button>
      <button class="tab" role="tab" id="t-lin" aria-controls="p-lin" aria-selected="false">Linux</button>
    </div>
    <div class="panel" id="p-win" role="tabpanel" aria-labelledby="t-win">
      <p class="where-run">Open <b>PowerShell</b> (Start → type <i>PowerShell</i>). No admin rights needed.</p>
      <div class="cmd"><code id="c-win">{{win_cmd}}</code>
        <button class="copy" type="button" data-copy="c-win">Copy</button></div>
      <p class="hint">Paste with <kbd>Ctrl</kbd> + <kbd>V</kbd> and press <kbd>Enter</kbd>.</p>
    </div>
    <div class="panel" id="p-mac" role="tabpanel" aria-labelledby="t-mac" hidden>
      <p class="where-run">Open <b>Terminal</b> (⌘ Space → type <i>Terminal</i>).</p>
      <div class="cmd"><code id="c-mac">{{nix_cmd}}</code>
        <button class="copy" type="button" data-copy="c-mac">Copy</button></div>
      <p class="hint">Paste with <kbd>⌘</kbd> + <kbd>V</kbd> and press <kbd>Return</kbd>.</p>
    </div>
    <div class="panel" id="p-lin" role="tabpanel" aria-labelledby="t-lin" hidden>
      <p class="where-run">Open a <b>terminal</b>. Works on Ubuntu, Debian, Fedora and Arch.</p>
      <div class="cmd"><code id="c-lin">{{nix_cmd}}</code>
        <button class="copy" type="button" data-copy="c-lin">Copy</button></div>
      <p class="hint">Paste with <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>V</kbd> and press <kbd>Enter</kbd>.</p>
    </div>
    <p class="sr" role="status" aria-live="polite" id="copy-status"></p>
  </section>

  <section class="card" aria-labelledby="happen-h">
    <div class="grid2">
      <div>
        <div class="label" id="happen-h">2 · What happens next</div>
        <ol class="steps" style="margin-top:14px">
          <li><div><b>Installs NAVIG</b><span>Downloads the official installer with its own runtime.
            Nothing else on the computer is changed.</span></div></li>
          <li><div><b>Links to {{hub_name}}</b><span>Uses this network's shared key — you never type
            or see a password.</span></div></li>
          <li><div><b>Ready in about a minute</b><span>Starts in the background and with Windows,
            macOS or Linux from now on.</span></div></li>
        </ol>
      </div>
      <div class="qr">
        <div class="code">{{qr_svg}}</div>
        <p>On another device? Scan to open this page.</p>
        <span class="url">{{page_url}}</span>
      </div>
    </div>
  </section>

  <section class="card" aria-labelledby="get-h">
    <div class="label" id="get-h">3 · What you get</div>
    <div class="feats" style="margin-top:14px">
      <div class="feat"><b>One NAVIG, many devices</b><span>The new computer appears next to
        {{hub_name}} and stays linked.</span></div>
      <div class="feat"><b>Private by default</b><span>Linked only on this network. No public link,
        no cloud account.</span></div>
      <div class="feat"><b>Yours to shape</b><span>Add an AI provider, voice or Telegram later —
        whenever you want.</span></div>
    </div>
    <div class="label" style="margin-top:22px">After it finishes</div>
    <div class="after" style="margin-top:10px">
      <div>On {{hub_name}}, see it: <code>navig mesh peers</code></div>
      <div>On the new computer, personalize it: <code>navig init</code></div>
    </div>
  </section>
</main>
<footer>
  <span><span class="lock">●</span> Served by {{hub_name}} on your local network</span>
  <span>{{gateway_url}}</span>
  <span>Only devices on this network can open this page</span>
</footer>
<script>
(function () {
  var ua = navigator.userAgent || "";
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) document.body.classList.add("is-mobile");

  // Tabs — pre-select the visitor's own OS.
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  function select(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
  }
  var plat = ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || ua).toLowerCase();
  var pre = /mac|iphone|ipad/.test(plat) ? "t-mac" : /linux|x11|cros/.test(plat) && !/android/.test(plat) ? "t-lin" : "t-win";
  select(document.getElementById(pre));
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { select(t); });
    t.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      var next = tabs[(i + d + tabs.length) % tabs.length];
      select(next); next.focus(); e.preventDefault();
    });
  });

  // Copy — navigator.clipboard is undefined on plain-http LAN pages (not a
  // secure context), so fall back to a hidden textarea + execCommand('copy');
  // if even that fails, select the command so Ctrl+C works.
  var status = document.getElementById("copy-status");
  function legacyCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", "");
    ta.style.position = "fixed"; ta.style.opacity = "0"; ta.style.top = "0";
    document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  function selectText(el) {
    var r = document.createRange(); r.selectNodeContents(el);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  }
  function done(btn, ok, codeEl) {
    btn.classList.toggle("done", ok);
    btn.textContent = ok ? "Copied ✓" : "Press Ctrl+C";
    status.textContent = ok ? "Command copied to the clipboard" : "Command selected — press Ctrl+C to copy";
    if (!ok) selectText(codeEl);
    clearTimeout(btn._t);
    btn._t = setTimeout(function () { btn.classList.remove("done"); btn.textContent = "Copy"; }, 2200);
  }
  Array.prototype.forEach.call(document.querySelectorAll(".copy"), function (btn) {
    btn.addEventListener("click", function () {
      var codeEl = document.getElementById(btn.getAttribute("data-copy"));
      var text = codeEl.textContent.trim();
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(
          function () { done(btn, true, codeEl); },
          function () { done(btn, legacyCopy(text), codeEl); });
      } else {
        done(btn, legacyCopy(text), codeEl);
      }
    });
  });
})();
</script>
</body>
</html>
"""


def page_html(
    *,
    hub_name: str,
    gateway_url: str,
    win_cmd: str,
    nix_cmd: str,
    qr_svg: str,
    peer_count: int | None,
) -> str:
    """The /install page. All dynamic text is HTML-escaped; qr_svg is our own markup."""
    page_url = f"{gateway_url}/install"
    if peer_count is None:
        peer_summary = "Ready for new devices"
    elif peer_count == 0:
        peer_summary = "First device to join"
    elif peer_count == 1:
        peer_summary = "1 device linked"
    else:
        peer_summary = f"{peer_count} devices linked"
    host = gateway_url.split("://", 1)[-1]
    return render(
        PAGE_TEMPLATE,
        mark=NAVIG_MARK_SVG,
        hub_name=escape(hub_name),
        gateway_host=escape(host),
        gateway_url=escape(gateway_url),
        page_url=escape(page_url),
        peer_summary=escape(peer_summary),
        win_cmd=escape(win_cmd),
        nix_cmd=escape(nix_cmd),
        qr_svg=qr_svg,
    )


# ─────────────────────────── Windows join script ─────────────────────

# Runs under `irm …/install/windows.ps1 | iex`, i.e. IN the user's own shell:
# it must never call `exit` (that closes their window) — everything lives in
# one scriptblock and ends with `return`. The official installer DOES exit, so
# it runs as a child process. No `$ErrorActionPreference='Stop'` at top level
# for the same reason: errors are handled per step and reported, never thrown
# out into the user's session.
WINDOWS_SCRIPT = r"""# NAVIG — add this computer to {{hub_name}}
# Served by {{hub_name}} on your local network. Safe to run again.
& {
  $Gateway = '{{gateway_url}}'
  $Token   = '{{mesh_token}}'
  $HubName = '{{hub_name}}'
  $ProgressPreference = 'SilentlyContinue'
  # This runs inside the user's own window: put their environment back as we found it.
  $savedEnv = @{ PYTHONUTF8 = $env:PYTHONUTF8; NAVIG_SKIP_ONBOARDING = $env:NAVIG_SKIP_ONBOARDING; NAVIG_PROFILE = $env:NAVIG_PROFILE }
  $env:PYTHONUTF8 = '1'
  $env:NAVIG_SKIP_ONBOARDING = '1'
  try {

  # ── output engine ────────────────────────────────────────────────────
  # One live line per wait (spinner + elapsed), a ✓/✗ result line when it
  # ends, silence otherwise. A wait that shows NOTHING reads as a hang —
  # measured on a real join where step 3 sat silent for ~40 s.
  $t0 = Get-Date
  $Frames = [char[]]'⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
  $Spin = @{ Label = ''; T0 = [DateTime]::MinValue; I = 0; On = $false; W = 76 }
  $Ctx = @{ Port = 8789 }   # shared state: functions mutate this, never the caller's scope

  function Elapsed($since) {
    $s = [int]((Get-Date) - $since).TotalSeconds
    if ($s -ge 60) { return ('{0}m {1:d2}s' -f [int][Math]::Floor($s / 60), ($s % 60)) }
    return "${s}s"
  }
  function Say($t)  { Write-Host "  $t" }
  function Step($n, $t) { Write-Host ''; Write-Host ("  [$n/5] ") -ForegroundColor Cyan -NoNewline; Write-Host $t }
  function Ok($t)   { Write-Host "        ✓ $t" -ForegroundColor Green }
  function Note($t) { Write-Host "        • $t" -ForegroundColor DarkGray }
  function Warn($t) { Write-Host "        ✗ $t" -ForegroundColor Yellow }
  function Stop-Join($t) {
    Write-Host ''; Write-Host "  ✗ $t" -ForegroundColor Red
    Write-Host '    Nothing else was changed. Fix the above and run the same command again.' -ForegroundColor DarkGray
    Write-Host ''
  }
  function Spin-Start($label) {
    $Spin.Label = $label; $Spin.T0 = Get-Date; $Spin.I = 0; $Spin.On = $true; Spin-Draw
  }
  function Spin-Draw {
    if (-not $Spin.On) { return }
    $el = [int]((Get-Date) - $Spin.T0).TotalSeconds
    $f = $Frames[$Spin.I % $Frames.Length]; $Spin.I++
    $line = "        $f $($Spin.Label)  ${el}s"
    Write-Host ("`r" + $line.PadRight($Spin.W)) -NoNewline -ForegroundColor Cyan
  }
  function Spin-Stop($glyph, $text, $color) {
    if (-not $Spin.On) { return }
    $el = [int]((Get-Date) - $Spin.T0).TotalSeconds
    $Spin.On = $false
    $line = "        $glyph $text  (${el}s)"
    Write-Host ("`r" + $line.PadRight($Spin.W)) -ForegroundColor $color
  }
  # Runs a child quietly: stdout/stderr to $log, spinner live the whole time.
  function Invoke-Child($file, $argList, $log) {
    $p = Start-Process -FilePath $file -ArgumentList $argList -NoNewWindow -PassThru `
      -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    while (-not $p.HasExited) { Spin-Draw; Start-Sleep -Milliseconds 400 }
    return $p.ExitCode
  }
  function Show-LogTail($log, $lines) {
    foreach ($f in @($log, "$log.err")) {
      if ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) {
        Get-Content $f -Tail $lines | ForEach-Object { Write-Host "          $_" -ForegroundColor DarkGray }
      }
    }
  }
  function Test-Gateway {
    # Reads the real port from gateway.json when it exists; 8789 otherwise.
    $Ctx.Port = 8789
    $gwFile = Join-Path $env:USERPROFILE '.navig\gateway.json'
    if (Test-Path $gwFile) { try { $Ctx.Port = [int](Get-Content $gwFile -Raw | ConvertFrom-Json).port } catch {} }
    try { $null = Invoke-RestMethod -Uri "http://127.0.0.1:$($Ctx.Port)/health" -TimeoutSec 2; return $true } catch { return $false }
  }
  function Wait-Gateway($attempts) {
    # On success the ✓ line is printed here and the spinner is closed; on
    # failure the spinner stays OPEN for the caller to end (recovery or fail).
    for ($i = 1; $i -le $attempts; $i++) {
      if (Test-Gateway) { Spin-Stop '✓' "gateway answering · port $($Ctx.Port)" Green; return $true }
      $Spin.Label = "waiting for the gateway · $i/$attempts"
      Spin-Draw
      Start-Sleep -Seconds 1
    }
    if (Test-Gateway) { Spin-Stop '✓' "gateway answering · port $($Ctx.Port)" Green; return $true }
    return $false
  }

  Write-Host ''
  Write-Host '  N A V I G' -ForegroundColor Cyan
  Say "Adding $env:COMPUTERNAME to $HubName"
  Note "linked through $Gateway"
  $log = Join-Path $env:TEMP 'navig-join.log'

  # ── 1 · install ──────────────────────────────────────────────────────
  Step 1 'Install NAVIG'
  $navig = Join-Path $env:USERPROFILE '.navig\runtime\venv\Scripts\navig.exe'
  $installer = Join-Path $env:TEMP 'navig-install.ps1'
  $installed = $false
  try {
    Note 'downloading the official installer'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://navig.run/install.ps1' -OutFile $installer -TimeoutSec 60 | Out-Null
    # 'server' keeps a joined node private (no public tunnel). An explicitly
    # exported NAVIG_PROFILE is the operator's own choice — honour it instead
    # of overwriting it, and let the finally below restore the shell either way.
    if (-not $env:NAVIG_PROFILE) { $env:NAVIG_PROFILE = 'server' }
    $hostExe = (Get-Process -Id $PID).Path
    # -NoConfirm: no repair menu mid-join; output to a log so the join flow
    # stays one clean column instead of a wall of installer boxes.
    Spin-Start 'installing NAVIG (about a minute)'
    $code = Invoke-Child $hostExe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$installer`"", '-NoConfirm') $log
    $installed = ($code -eq 0) -and (Test-Path $navig)
    if ($installed) { Spin-Stop '✓' 'NAVIG installed' Green }
    else {
      Spin-Stop '✗' 'the installer reported a problem' Yellow
      Show-LogTail $log 12
    }
  } catch {
    if ($Spin.On) { Spin-Stop '✗' 'could not download the installer' Yellow }
    Warn "Could not download the installer: $($_.Exception.Message)"
  } finally {
    Remove-Item $installer -ErrorAction SilentlyContinue
  }
  if (-not $installed) {
    if (Test-Path $navig) {
      Note 'NAVIG is present anyway — continuing.'
    } else {
      Stop-Join 'NAVIG could not be installed. Check the internet connection of this computer.'
      return
    }
  }
  Ok ("NAVIG " + ((& $navig --version 2>$null) -join ' ').Trim())

  # ── 2 · link ─────────────────────────────────────────────────────────
  Step 2 "Link to $HubName"
  & $navig config set gateway.mesh_token $Token *> $null
  if ($LASTEXITCODE -ne 0) { Stop-Join 'Could not save the network key.'; return }
  & $navig config set onboarding.role node *> $null
  & $navig config set gateway.host 0.0.0.0 *> $null
  Ok 'Network key saved (never shown)'
  Note 'this device joins as a node · binds this network only'

  # ── 3 · start ────────────────────────────────────────────────────────
  Step 3 'Start the daemon'
  Spin-Start 'restarting the NAVIG service'
  $code = Invoke-Child $navig @('service', 'restart') $log
  if ($code -ne 0) {
    Spin-Stop '✗' 'restart did not confirm — trying a plain start' Yellow
    Spin-Start 'starting the NAVIG service'
    $code = Invoke-Child $navig @('service', 'start') $log
  }
  if ($code -ne 0) { Spin-Stop '✗' 'the service command failed' Yellow }
  else { Spin-Stop '✓' 'service told to run' Green }

  $port = 8789
  Spin-Start 'waiting for the gateway · 1/60'
  $healthy = Wait-Gateway 60
  if (-not $healthy) {
    # One recovery round: a stuck stop/start clears flags, re-enables the
    # autostart task and gives a wedged first boot a second chance.
    Spin-Stop '✗' 'no answer yet — trying one clean restart' Yellow
    Spin-Start 'recovery: stop'
    Invoke-Child $navig @('service', 'stop') $log | Out-Null
    Spin-Stop '•' 'stopped' DarkGray
    Spin-Start 'recovery: start'
    Invoke-Child $navig @('service', 'start') $log | Out-Null
    Spin-Stop '•' 'started' DarkGray
    Spin-Start 'waiting for the gateway · 1/30'
    $healthy = Wait-Gateway 30
  }
  $port = $Ctx.Port
  if (-not $healthy) {
    if ($Spin.On) { Spin-Stop '✗' 'the gateway never answered' Yellow }
    Warn 'The gateway is not up yet.'
    Note "output: $log"
    Note 'next:   navig service logs      (then run this command again)'
  }

  # ── 4 · reachability ─────────────────────────────────────────────────
  Step 4 'Open your local network'
  $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($isAdmin) {
    try {
      if (-not (Get-NetFirewallRule -DisplayName 'NAVIG (local network)' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName 'NAVIG (local network)' -Direction Inbound -Protocol TCP `
          -LocalPort $port -Profile Private -RemoteAddress LocalSubnet -Action Allow | Out-Null
      }
      Ok 'Firewall allows your local network only'
    } catch { Warn "Firewall rule not added: $($_.Exception.Message)" }
  } else {
    Note 'not administrator — Windows may block other devices from reaching this one'
  }

  # ── 5 · announce ─────────────────────────────────────────────────────
  Step 5 "Say hello to $HubName"
  $myIp = $null
  try {
    $hubHost = ([Uri]$Gateway).Host
    $udp = New-Object System.Net.Sockets.UdpClient
    $udp.Connect($hubHost, 9)
    $myIp = $udp.Client.LocalEndPoint.Address.ToString()
    $udp.Close()
  } catch {}
  $self = if ($myIp) { "http://$($myIp):$port" } else { "http://localhost:$port" }
  $joined = $false
  $why = ''
  if (-not $healthy) {
    $why = 'the local gateway is not answering, so NEURON was not asked to link'
  } else {
    Note "knocking on $HubName — up to 20 seconds"
    for ($attempt = 1; $attempt -le 2 -and -not $joined; $attempt++) {
      try {
        $resp = Invoke-RestMethod -Method Post -Uri "$Gateway/mesh/ping" -TimeoutSec 20 `
          -Headers @{ Authorization = "Bearer $Token" } -ContentType 'application/json' `
          -Body (@{ gateway_url = $self } | ConvertTo-Json)
        $joined = [bool]$resp.ok
      } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -ge 500) {
          $why = "$HubName could not reach this computer at $self — usually the firewall (port $port)"
        } elseif ($code) {
          $why = "$HubName replied with HTTP $code"
        } else {
          $why = "could not reach $HubName ($($_.Exception.Message))"
        }
        if ($attempt -lt 2) { Start-Sleep -Seconds 2 }
      }
    }
  }

  Write-Host ''
  if ($joined) {
    Write-Host "  ✓ $env:COMPUTERNAME is now part of $HubName" -ForegroundColor Green
  } else {
    if ($healthy) {
      Write-Host '  ✓ NAVIG is installed and running on this computer' -ForegroundColor Green
      Warn "$HubName could not link yet: $why"
      if (-not $isAdmin) {
        Note 'Most often Windows Firewall. Run the same command once in an'
        Note 'administrator PowerShell to allow your local network.'
      }
    } else {
      Write-Host '  ✗ NAVIG is installed, but the gateway is not up yet' -ForegroundColor Yellow
      Note 're-run this command once:  navig service status   shows a daemon'
    }
  }
  Write-Host ''
  Write-Host ("  " + ('-' * 52)) -ForegroundColor DarkGray
  if ($joined) { Ok "done in $(Elapsed $t0)" } else { Warn "finished with warnings in $(Elapsed $t0)" }
  Write-Host ''
  Note "on ${HubName}:   navig mesh peers"
  Note 'on this computer, in a NEW terminal:  navig init   (optional: AI, voice, Telegram)'
  Write-Host ''

  } finally {
    foreach ($k in $savedEnv.Keys) {
      if ($null -eq $savedEnv[$k]) { Remove-Item "Env:\$k" -ErrorAction SilentlyContinue }
      else { Set-Item "Env:\$k" $savedEnv[$k] }
    }
  }
}
"""


# ─────────────────────────── macOS / Linux join script ───────────────

UNIX_SCRIPT = r"""#!/usr/bin/env bash
# NAVIG — add this computer to {{hub_name}}
# Served by {{hub_name}} on your local network. Safe to run again.
set -u
GATEWAY='{{gateway_url}}'
TOKEN='{{mesh_token}}'
HUB='{{hub_name}}'
export NAVIG_SKIP_ONBOARDING=1

if [ -t 1 ]; then C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'; TTY=1
else C=''; G=''; Y=''; R=''; D=''; N=''; TTY=0; fi

# ── output engine: one live line per wait, a result line when it ends ────────
T0=$(date +%s)
FRAMES='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
SPIN_ON=0; SPIN_LABEL=''; SPIN_T0=0; SPIN_I=0
JOINED=0
elapsed() { local s=$(( $(date +%s) - $1 )); if [ "$s" -ge 60 ]; then printf '%dm %02ds' $((s / 60)) $((s % 60)); else printf '%ds' "$s"; fi; }
step()   { printf '\n  %s[%s/4]%s %s\n' "$C" "$1" "$N" "$2"; }
ok()     { printf '        %s✓ %s%s\n' "$G" "$1" "$N"; }
note()   { printf '        %s• %s%s\n' "$D" "$1" "$N"; }
warn()   { printf '        %s✗ %s%s\n' "$Y" "$1" "$N"; }
die()    { printf '\n  %s✗ %s%s\n    %sNothing else was changed. Fix the above and run the same command again.%s\n\n' "$R" "$1" "$N" "$D" "$N"; exit 1; }
spin_start() { SPIN_ON=1; SPIN_LABEL=$1; SPIN_T0=$(date +%s); SPIN_I=0; spin_draw; }
spin_draw() {
  { [ "$TTY" = 1 ] && [ "$SPIN_ON" = 1 ]; } || return 0
  local f=${FRAMES:$((SPIN_I % ${#FRAMES})):1}
  SPIN_I=$((SPIN_I + 1))
  printf '\r        %s %s  %2ds        ' "$f" "$SPIN_LABEL" "$(( $(date +%s) - SPIN_T0 ))"
}
spin_stop() { # <ansi-colour> <text> — prints the result line even when piped
  [ "$SPIN_ON" = 1 ] || return 0
  SPIN_ON=0
  local el=$(( $(date +%s) - SPIN_T0 ))
  if [ "$TTY" = 1 ]; then
    printf '\r        \033[%sm✓ %s  %ds        \033[0m\n' "$1" "$2" "$el"
  else
    printf '        ✓ %s  %ds\n' "$2" "$el"
  fi
}
spin_fail() { # <text>
  [ "$SPIN_ON" = 1 ] || return 0
  SPIN_ON=0
  local el=$(( $(date +%s) - SPIN_T0 ))
  if [ "$TTY" = 1 ]; then
    printf '\r        \033[33m✗ %s  %ds        \033[0m\n' "$2" "$el"
  else
    printf '        ✗ %s  %ds\n' "$2" "$el"
  fi
}
# run_child <log> <cmd...> — quiet child, spinner live; returns the child's code
run_child() {
  local log=$1; shift
  ( "$@" >"$log" 2>&1 ) &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do spin_draw; sleep 0.4; done
  wait "$pid"; return $?
}

printf '\n  %sN A V I G%s\n  Adding %s to %s\n' "$C" "$N" "$(hostname)" "$HUB"
note "linked through $GATEWAY"
LOG=${TMPDIR:-/tmp}/navig-join.log

step 1 'Install NAVIG'
note 'downloading and running the official installer'
if curl -fsSL https://navig.run/install.sh | NAVIG_PROFILE=server bash >"$LOG" 2>&1; then :; else
  warn 'The installer reported a problem.'
  tail -n 12 "$LOG" 2>/dev/null | sed 's/^/          /'
fi
NAVIG="$HOME/.local/bin/navig"
[ -x "$NAVIG" ] || NAVIG="$(command -v navig || true)"
[ -n "$NAVIG" ] && [ -x "$NAVIG" ] || die 'NAVIG could not be installed. Check the internet connection of this computer.'
ok "NAVIG $("$NAVIG" --version 2>/dev/null | head -n1)"

step 2 "Link to $HUB"
"$NAVIG" config set gateway.mesh_token "$TOKEN" >/dev/null 2>&1 || die 'Could not save the network key.'
"$NAVIG" config set onboarding.role node >/dev/null 2>&1
"$NAVIG" config set gateway.host 0.0.0.0 >/dev/null 2>&1
ok 'Network key saved (never shown)'

step 3 'Start the daemon'
spin_start 'restarting the NAVIG service'
if run_child "$LOG" "$NAVIG" service restart; then
  spin_stop 32 'service told to run'
else
  spin_fail 'restart did not confirm — trying a plain start'
  spin_start 'starting the NAVIG service'
  if run_child "$LOG" "$NAVIG" service start; then spin_stop 32 'service told to run'; else spin_fail 'the service command failed'; fi
fi
PORT=8789
gateway_port() {
  PORT=8789
  if [ -f "$HOME/.navig/gateway.json" ]; then
    local p; p=$(sed -n 's/.*"port"[^0-9]*\([0-9][0-9]*\).*/\1/p' "$HOME/.navig/gateway.json" | head -n1)
    [ -n "$p" ] && PORT=$p
  fi
}
wait_gateway() { # attempts → 0 when answering (spinner closed inside), 1 with spinner still open
  local i=1
  while [ "$i" -le "$1" ]; do
    gateway_port
    if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      spin_stop 32 "gateway answering · port $PORT"; return 0
    fi
    SPIN_LABEL="waiting for the gateway · $i/$1"
    spin_draw
    sleep 1
    i=$((i + 1))
  done
  gateway_port
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    spin_stop 32 "gateway answering · port $PORT"; return 0
  fi
  return 1
}
spin_start 'waiting for the gateway · 1/60'
HEALTHY=1
wait_gateway 60 || HEALTHY=0
if [ "$HEALTHY" = 0 ]; then
  spin_fail 'no answer yet — trying one clean restart'
  spin_start 'recovery: stop'
  run_child "$LOG" "$NAVIG" service stop || true
  spin_stop 2 'stopped'
  spin_start 'recovery: start'
  run_child "$LOG" "$NAVIG" service start || true
  spin_stop 2 'started'
  spin_start 'waiting for the gateway · 1/30'
  if wait_gateway 30; then HEALTHY=1; else HEALTHY=0; fi
fi
if [ "$HEALTHY" = 0 ]; then
  spin_fail 'the gateway never answered'
  warn 'The gateway is not up yet.'
  note "output: $LOG"
  note 'next:   navig service logs      (then run this command again)'
fi

step 4 "Say hello to $HUB"
if [ "$HEALTHY" != 1 ]; then
  note 'skipped — the local gateway is not answering yet'
  warn 're-run this command once the daemon is up.'
else
  HUB_HOST=$(printf '%s' "$GATEWAY" | sed -E 's#^[a-z]+://\[?([^]/:]+)\]?.*#\1#')
  MY_IP=$(ip route get "$HUB_HOST" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)
  if [ -z "$MY_IP" ] && command -v route >/dev/null 2>&1; then
    IFACE=$(route -n get "$HUB_HOST" 2>/dev/null | awk '/interface:/{print $2}')
    [ -n "$IFACE" ] && MY_IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null)
  fi
  SELF="http://${MY_IP:-localhost}:$PORT"
  note "knocking on $HUB — up to 20 seconds"
  BODY=${TMPDIR:-/tmp}/navig-join-ping.$$
  JOINED=0; WHY=''
  attempt=1
  while [ "$attempt" -le 2 ] && [ "$JOINED" = 0 ]; do
    CODE=$(curl -sS -m 20 -X POST "$GATEWAY/mesh/ping" -o "$BODY" -w '%{http_code}' \
      -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
      -d "{\"gateway_url\":\"$SELF\"}" 2>>"$LOG" || true)
    CODE=${CODE:-000}   # curl prints 000 itself on connect failure; guard the empty case
    RESP=$(cat "$BODY" 2>/dev/null || true)
    if printf '%s' "$RESP" | grep -q '"ok": *true'; then JOINED=1; break; fi
    case "$CODE" in
      5*) WHY="$HUB could not reach this computer at $SELF — usually a firewall (port $PORT)" ;;
      000) WHY="could not reach $HUB (network error)" ;;
      *) WHY="$HUB replied with HTTP $CODE" ;;
    esac
    attempt=$((attempt + 1))
    [ "$JOINED" = 0 ] && [ "$attempt" -le 2 ] && sleep 2
  done
  rm -f "$BODY"
  printf '\n'
  if [ "$JOINED" = 1 ]; then
    printf '  %s✓ %s is now part of %s%s\n' "$G" "$(hostname)" "$HUB" "$N"
  else
    printf '  %s✓ NAVIG is installed and running on this computer%s\n' "$G" "$N"
    warn "$HUB could not link yet: $WHY"
  fi
fi
printf '\n'
printf '  %s%s%s\n' "$D" '----------------------------------------------------' "$N"
if [ "$JOINED" = 1 ] 2>/dev/null; then ok "done in $(elapsed "$T0")"; else warn "finished with warnings in $(elapsed "$T0")"; fi
printf '\n'
note "on $HUB:   navig mesh peers"
note 'on this computer, in a NEW terminal:  navig init   (optional: AI, voice, Telegram)'
printf '\n'
"""


def windows_script(*, hub_name: str, gateway_url: str, mesh_token: str) -> str:
    return render(
        WINDOWS_SCRIPT,
        hub_name=_ps_literal(hub_name),
        gateway_url=_ps_literal(gateway_url),
        mesh_token=_ps_literal(mesh_token),
    )


def unix_script(*, hub_name: str, gateway_url: str, mesh_token: str) -> str:
    return render(
        UNIX_SCRIPT,
        hub_name=_sh_literal(hub_name),
        gateway_url=_sh_literal(gateway_url),
        mesh_token=_sh_literal(mesh_token),
    )


def _ps_literal(value: str) -> str:
    """Body of a PowerShell single-quoted string ('' is the only escape)."""
    return value.replace("'", "''")


def _sh_literal(value: str) -> str:
    """Body of a POSIX single-quoted string — close, escape, reopen."""
    return value.replace("'", "'\\''")
