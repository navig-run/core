"""Session-first website auto-login engine.

Given an *already-attached* browser controller (a ``BrowserController`` /
``CDPBridge`` positioned on a page), sign the user in with the strongest method
available, in this order:

1. **Origin-bind** the current page to a stored credential (``origin_match``).
   Nothing happens on a non-matching / non-https origin — the anti-phishing floor.
2. **Session-first** — if a fresh authenticated session (cookies + localStorage)
   is vaulted for this site, restore it and reload. If the login wall is gone,
   we're done without ever typing a password.
3. **Heuristic form-fill** — detect the username + password fields in the page
   and fill them. The secret is passed into ``page.evaluate`` as a structured
   argument; it never enters an LLM context and is never logged.
4. On a confirmed login, **re-capture the session** into the vault so next time
   is a pure restore.

Security invariants:
- The password is injected **server-side**; it is never returned to the caller,
  never logged, never placed in a tool result.
- A credential fills **only** on its bound registrable domain over https.
- A form with 0 or >1 password fields (a register / change-password form) is
  never auto-filled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from .origin_match import credential_matches, registrable_domain

if TYPE_CHECKING:  # pragma: no cover
    from .controller import BrowserController

__all__ = ["auto_login"]


# JS that detects a single login form and fills it. Receives credentials as a
# structured evaluate() argument (never string-interpolated) and returns only
# non-secret booleans about what it found.
_FILL_JS = r"""
(creds) => {
  const vis = (el) => el && el.offsetParent !== null && !el.disabled && el.type !== 'hidden';
  const pwds = Array.from(document.querySelectorAll("input[type='password']")).filter(vis);
  const out = { hasPassword: pwds.length > 0, isMultiPassword: pwds.length > 1,
                filled: false, usernameFound: false, hasSubmit: false };
  if (pwds.length !== 1) return out;            // 0 or >1 → not a plain login form
  const pwd = pwds[0];
  const form = pwd.form || document;
  const setVal = (el, val) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const cands = Array.from(form.querySelectorAll('input')).filter(
    (i) => vis(i) && i.type !== 'password' &&
           ['text', 'email', 'tel', 'search', ''].includes((i.type || '').toLowerCase()));
  let user = cands.find((i) =>
    /user|email|login|account|phone|ident/i.test(
      (i.name || '') + (i.id || '') + (i.autocomplete || '') + (i.getAttribute('aria-label') || '')));
  if (!user) {
    const before = cands.filter((i) =>
      pwd.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_PRECEDING);
    user = before.length ? before[before.length - 1] : (cands[0] || null);
  }
  if (user) { setVal(user, creds.u); out.usernameFound = true; }
  setVal(pwd, creds.p);
  out.filled = true;
  let submit = form.querySelector("button[type='submit'], input[type='submit']");
  if (!submit) {
    submit = Array.from(form.querySelectorAll("button, input[type='button'], [role='button']"))
      .find((b) => /log ?in|sign ?in|continue|submit|next|anmelden|connexion/i
        .test((b.innerText || b.value || '')));
  }
  if (submit) { submit.setAttribute('data-navig-submit', '1'); out.hasSubmit = true; }
  return out;
}
"""

# A login wall is present if a password field OR a 2FA/OTP field is visible.
# (Narrow OTP selectors here — avoid false positives like a "promo code" input —
# so we only treat a genuine one-time-code step as "not yet logged in".)
_LOGIN_WALL_JS = r"""
() => {
  const vis = (el) => el && el.offsetParent !== null;
  const pwd = Array.from(document.querySelectorAll("input[type='password']")).some(vis);
  const otp = Array.from(document.querySelectorAll(
    "input[autocomplete='one-time-code'], input[name*='otp' i], input[id*='otp' i], " +
    "input[name*='2fa' i], input[id*='2fa' i]"))
    .some((el) => vis(el) && el.type !== 'password');
  return pwd || otp;
}
"""

# Fills a one-time-code (TOTP) step. Handles a single OTP input and split
# one-digit-per-box layouts. Receives the code as a structured arg.
_OTP_FILL_JS = r"""
(code) => {
  const vis = (el) => el && el.offsetParent !== null && !el.disabled && el.type !== 'hidden';
  const setVal = (el, val) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const out = { found: false, filled: false, hasSubmit: false };
  const digits = String(code).split('');
  let one = Array.from(document.querySelectorAll(
    "input[autocomplete='one-time-code'], input[name*='otp' i], input[id*='otp' i], " +
    "input[name*='code' i], input[id*='code' i], input[name*='token' i], " +
    "input[name*='2fa' i], input[id*='2fa' i], input[name*='verif' i]"))
    .filter((i) => vis(i) && i.type !== 'password');
  if (one.length === 1) {
    out.found = true; setVal(one[0], String(code)); out.filled = true;
  } else {
    const boxes = Array.from(document.querySelectorAll("input[maxlength='1']")).filter(vis);
    if (boxes.length >= digits.length && boxes.length <= 8) {
      out.found = true;
      boxes.forEach((b, i) => { if (i < digits.length) setVal(b, digits[i]); });
      out.filled = digits.length > 0;
    }
  }
  if (out.filled) {
    const form = document.querySelector('form') || document;
    let submit = form.querySelector("button[type='submit'], input[type='submit']");
    if (!submit) {
      submit = Array.from(document.querySelectorAll("button, input[type='button'], [role='button']"))
        .find((b) => /verify|confirm|submit|continue|log ?in|sign ?in|next/i
          .test((b.innerText || b.value || '')));
    }
    if (submit) { submit.setAttribute('data-navig-otp-submit', '1'); out.hasSubmit = true; }
  }
  return out;
}
"""


# Hosts / paths that mean "still on a sign-in flow" even when no password field is
# shown yet (e.g. Google's email-first step, OAuth consent, SSO hand-offs).
_SIGN_IN_MARKERS = (
    "accounts.google.com", "login.microsoftonline.com", "login.live.com",
    "signin", "sign-in", "/login", "/auth/", "oauth", "/sso",
)


def _is_sign_in_url(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in _SIGN_IN_MARKERS)


async def _is_logged_in(controller: "BrowserController") -> bool:
    """Heuristic: past the login wall only if BOTH are true —
    no visible password/2FA field remains, AND we are not sitting on a known
    sign-in URL (an email-first step shows no password field but is NOT logged in).
    """
    try:
        current = await controller.get_url()
        if _is_sign_in_url(current):
            return False
        wall = await controller._page.evaluate(_LOGIN_WALL_JS)  # noqa: SLF001
        return not bool(wall)
    except Exception:  # noqa: BLE001
        return False


async def _try_totp(controller: "BrowserController", secret: str) -> bool:
    """If a 2FA/OTP step is on the page, fill the code from a stored TOTP secret.

    Returns True if a code was filled (and submit attempted). The generated code
    is passed into ``page.evaluate`` as an argument and is never logged.
    """
    from ..vault.totp import is_valid_secret, totp_now  # noqa: PLC0415

    if not is_valid_secret(secret):
        logger.warning("[autofill] stored TOTP secret is not valid base32 — skipping 2FA")
        return False
    try:
        code = totp_now(secret)
        res = await controller._page.evaluate(_OTP_FILL_JS, code)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("[autofill] TOTP fill failed: {}", exc)
        return False
    if not res.get("filled"):
        return False
    try:
        if res.get("hasSubmit"):
            await controller._page.click("[data-navig-otp-submit='1']")  # noqa: SLF001
        else:
            await controller._page.keyboard.press("Enter")  # noqa: SLF001
        await controller.wait_for_stable()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[autofill] TOTP submit failed: {}", exc)
    return True


async def auto_login(
    controller: "BrowserController",
    *,
    domain: str | None = None,
    username: str | None = None,
    allow_insecure: bool = False,
    auto_submit: bool = True,
    vault: Any = None,
) -> dict[str, Any]:
    """Sign in on the controller's current page. Returns a structured result.

    ``status`` is one of: ``session_restored``, ``logged_in``, ``filled``,
    ``no_credential``, ``needs_disambiguation``, ``wrong_origin``,
    ``no_login_form``, ``error``. The password is never included in the result.
    """
    from ..vault import (  # noqa: PLC0415
        find_logins_for_domain,
        get_session,
        resolve_login,
        save_session,
    )

    try:
        page_url = await controller.get_url()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"no active page: {exc}"}

    target = registrable_domain(domain) if domain else registrable_domain(page_url)
    if not target:
        return {"status": "error", "error": "could not determine target domain"}

    # Origin floor #1: the current page must be a valid https origin for `target`.
    if not credential_matches(page_url, target, allow_insecure=allow_insecure):
        return {"status": "wrong_origin", "page_url": page_url, "domain": target,
                "detail": "current page is not a secure match for the target domain"}

    login, status = resolve_login(target, username, vault=vault)
    if status == "needs_disambiguation":
        accounts = [i.username for i in find_logins_for_domain(target, vault=vault)]
        return {"status": "needs_disambiguation", "domain": target, "accounts": accounts}

    # ── 1. Session-first — restore a captured session if we have one. A session
    #    authenticates on its own, so try it BEFORE requiring a stored credential:
    #    a session-only login (e.g. games/Epic — a vaulted session, no password)
    #    would otherwise bail at ``no_credential`` and never restore. Origin
    #    floor #1 above already bound the page to ``target`` and the session's own
    #    domain is ``target``, so this stays origin-safe without floor #2. ──
    sess_acct = username if username is not None else (login.username if login else None)
    session = get_session(target, sess_acct, vault=vault)
    if session and session.storage_state:
        try:
            await controller.restore_storage_state(session.storage_state)
            await controller.reload()
            await controller.wait_for_stable()
            if await _is_logged_in(controller):
                acct_name = session.username or sess_acct
                logger.info("[autofill] session restored for {} ({})", target, acct_name)
                return {"status": "session_restored", "domain": target, "username": acct_name}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[autofill] session restore failed for {}: {}", target, exc)

    # No usable session — a credential form-fill needs a stored login.
    if login is None:
        return {"status": "no_credential", "domain": target}

    # Origin floor #2: the stored credential's own domain must bind to this page.
    if not credential_matches(page_url, login.domain, allow_insecure=allow_insecure):
        return {"status": "wrong_origin", "page_url": page_url, "domain": login.domain}

    acct = login.username

    # ── 2. Heuristic form-fill (secret passed as a structured arg, never logged) ──
    try:
        found = await controller._page.evaluate(  # noqa: SLF001
            _FILL_JS, {"u": acct, "p": login.password}
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"fill failed: {exc}", "domain": target}

    if not found.get("hasPassword"):
        return {"status": "no_login_form", "domain": target,
                "detail": "no visible password field on this page"}
    if found.get("isMultiPassword"):
        return {"status": "no_login_form", "domain": target,
                "detail": "multiple password fields (register/change form) — not auto-filled"}
    if not found.get("filled"):
        return {"status": "no_login_form", "domain": target}

    submitted = False
    if auto_submit:
        try:
            if found.get("hasSubmit"):
                await controller._page.click("[data-navig-submit='1']")  # noqa: SLF001
            else:
                await controller._page.keyboard.press("Enter")  # noqa: SLF001
            submitted = True
            await controller.wait_for_stable()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[autofill] submit failed for {}: {}", target, exc)

    # ── 3. 2FA/TOTP — if a code step appeared and we hold the secret, clear it ──
    totp_used = False
    if submitted and login.totp_secret and not await _is_logged_in(controller):
        totp_used = await _try_totp(controller, login.totp_secret)

    # ── 4. Verify + re-capture the session on a confirmed login ──
    if submitted and await _is_logged_in(controller):
        try:
            state = await controller.export_storage_state()
            save_session(target, state, username=acct, vault=vault)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[autofill] session capture failed for {}: {}", target, exc)
        logger.info("[autofill] logged in to {} ({}){}",
                    target, acct, " [2fa]" if totp_used else "")
        return {"status": "logged_in", "domain": target, "username": acct,
                "username_filled": bool(found.get("usernameFound")), "totp_used": totp_used}

    # Filled but could not confirm — do NOT resubmit (account-lockout guard).
    detail = "credentials filled; login not confirmed"
    if login.totp_secret and not totp_used:
        detail += " (2FA step not detected)"
    else:
        detail += " (2FA/captcha or slow page?)"
    return {"status": "filled", "domain": target, "username": acct,
            "submitted": submitted, "username_filled": bool(found.get("usernameFound")),
            "totp_used": totp_used, "detail": detail}
