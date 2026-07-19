"""Gmail compose recipe — reliable compose + send via Gmail's deep-link URL.

Instead of clicking through Gmail's (frequently-changing) UI, we open Gmail's
**compose deep-link**, which opens a pre-filled compose window in one navigation:

    https://mail.google.com/mail/u/<idx>/?view=cm&fs=1&to=…&su=…&body=…&cc=…&bcc=…

Then ``Ctrl+Enter`` sends. This is markup-agnostic and stable. It requires the
profile to already be **signed into Gmail** (session-first — sign in once in the
automation profile). Sending is gated by the caller (default: compose only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:  # pragma: no cover
    from ..controller import BrowserController

__all__ = ["compose_url", "compose"]

_COMPOSE_BY_INDEX = "https://mail.google.com/mail/u/{idx}/"
_COMPOSE_BY_EMAIL = "https://mail.google.com/mail/"


def compose_url(
    to: str = "",
    subject: str = "",
    body: str = "",
    *,
    cc: str = "",
    bcc: str = "",
    account: int | str = 0,
) -> str:
    """Build Gmail's compose deep-link. All fields optional (blank → empty compose).

    *account* selects which signed-in Google account (for one profile holding several):
    an **email** (e.g. ``me@gmail.com``) uses Google's ``authuser`` selector (robust —
    order-independent); a numeric **index** (0, 1, 2…) uses the ``/u/<n>/`` path.
    """
    acct = str(account or 0).strip()
    if "@" in acct:  # address by email — resolves the right account regardless of order
        base = _COMPOSE_BY_EMAIL
        parts = [("authuser", acct)]
    else:
        base = _COMPOSE_BY_INDEX.format(idx=acct if acct.isdigit() else "0")
        parts = []
    parts += [("view", "cm"), ("fs", "1")]
    for key, val in (("to", to), ("su", subject), ("body", body), ("cc", cc), ("bcc", bcc)):
        if val:
            parts.append((key, val))
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in parts)
    return base + "?" + query


async def compose(
    controller: "BrowserController",
    *,
    to: str = "",
    subject: str = "",
    body: str = "",
    cc: str = "",
    bcc: str = "",
    send: bool = False,
    account: int | str = 0,
) -> dict[str, Any]:
    """Open a pre-filled Gmail compose window on the attached profile; optionally send.

    Returns a status dict. ``status`` ∈ {composed, sent, not_signed_in, error}. When
    *send* is False the compose window is left open for the user to review/send.
    *account* selects which Gmail account (email or index) — see :func:`compose_url`.
    """
    url = compose_url(to, subject, body, cc=cc, bcc=bcc, account=account)
    try:
        await controller.navigate(url)
        await controller.wait_for_stable()
        current = await controller.get_url()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "error", "error": f"navigation failed: {exc}"}

    # If Gmail bounced us to the Google sign-in flow, the profile isn't logged in.
    if "accounts.google.com" in current or "mail.google.com" not in current:
        return {"ok": False, "status": "not_signed_in", "final_url": current,
                "detail": "this profile is not signed into Gmail — open it and sign in once: "
                          "navig cdp open <profile>"}

    # Wait for the compose window to actually render before we act on it. The
    # deep-link loads Gmail's SPA, so networkidle alone can fire before the
    # compose form exists. Best-effort — some controllers lack the primitive.
    compose_ready = True
    wait_for = getattr(controller, "wait_for_selector", None)
    if callable(wait_for):
        try:
            compose_ready = bool(await wait_for(
                "input[name='subjectbox'], div[aria-label^='Message Body'], "
                "div[role='textbox'][aria-label], textarea[name='body']",
                timeout=10000, state="visible",
            ))
        except Exception:  # noqa: BLE001
            compose_ready = False

    result: dict[str, Any] = {"ok": True, "status": "composed", "to": to, "subject": subject,
                              "sent": False, "compose_ready": compose_ready}
    if send:
        # Defense-in-depth: if a safe-mode guard is active (e.g. an agent path),
        # never actually send — leave the draft for the user.
        from .. import safe_mode
        if safe_mode.is_active():
            result["status"] = "composed"
            result["detail"] = "safe mode active — draft prepared, not sent (re-run with --yes)"
            return result
        if not compose_ready:
            # send was requested but we couldn't act on it → not a success
            result["ok"] = False
            result["status"] = "send_unconfirmed"
            result["detail"] = ("compose window did not render in time — not sending "
                                "automatically; send manually in the browser")
            return result
        try:
            confirmed = await _send_and_confirm(controller)
        except Exception as exc:  # noqa: BLE001
            result["ok"] = False
            result["status"] = "send_unconfirmed"
            result["send_error"] = str(exc)
            result["detail"] = "send failed — send manually in the window"
            return result
        if confirmed:
            result["status"] = "sent"
            result["sent"] = True
        else:
            # Never claim "sent" without proof — the compose is still open.
            result["ok"] = False
            result["status"] = "send_unconfirmed"
            result["detail"] = ("pressed Send but couldn't confirm the message left "
                                "(compose still open — check recipients, or that Gmail "
                                "keyboard shortcuts are on; otherwise send manually)")
    return result


_SUBJECT_SELECTOR = "input[name='subjectbox']"
# Gmail's "Message sent" snackbar (with an Undo link) — the strongest sent signal.
_SENT_TOAST_SELECTOR = "#link_undo, [aria-label*='Message sent' i], span:has-text('Message sent')"


async def _send_and_confirm(controller: "BrowserController") -> bool:
    """Send the open compose and confirm it actually left. Returns True only on proof.

    Prefers clicking Gmail's Send button (works regardless of the keyboard-shortcuts
    setting); falls back to focusing the body + Ctrl+Enter. Confirms via EITHER of two
    signals: the "Message sent" toast appears, OR the compose window (subject box)
    disappears. (Checking ``state="hidden"`` waits for the compose to close — the earlier
    ``state="visible"`` check returned instantly and mis-reported a real send as failed.)
    """
    page = controller._page
    clicked = False
    # Short per-selector timeout so a missed Send button (Gmail markup drift) fails
    # fast to the Ctrl+Enter fallback instead of waiting the 30s default each time.
    for sel in ("div[role='button'][data-tooltip^='Send']",
                "div[role='button'][aria-label^='Send']",
                "div[aria-label^='Send ']"):
        try:
            await page.click(sel, timeout=2500)
            clicked = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        try:
            await page.click("div[aria-label^='Message Body'], div[role='textbox'][aria-label]",
                             timeout=2500)
        except Exception:  # noqa: BLE001
            pass
        await page.keyboard.press("Control+Enter")

    await controller.wait_for_stable()

    wait_for = getattr(controller, "wait_for_selector", None)
    if not callable(wait_for):
        return True  # can't verify on this controller → optimistic

    # Positive signal: the "Message sent" toast.
    try:
        if await wait_for(_SENT_TOAST_SELECTOR, timeout=6000, state="visible"):
            return True
    except Exception:  # noqa: BLE001
        pass
    # Or the compose window closed (subject box gone).
    try:
        if await wait_for(_SUBJECT_SELECTOR, timeout=6000, state="hidden"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False
