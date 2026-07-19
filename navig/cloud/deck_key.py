"""Durable recovery for ``deck.api_key`` — the identity of the whole install.

``deck.api_key`` is not just a bearer token. Its **sha256 IS the Lighthouse tenant**
(the Durable Object the edge routes Telegram/SMS/Signals traffic to), and its raw
value is embedded in the Mini App link. Losing it does not merely log you out — it
**re-identifies the install**: the bot's mailbox moves, and the edge happily accepts
traffic for the old tenant (queues it, acks ``202``), so nothing errors and the
messages are simply gone. See :mod:`navig.cloud.rotation`.

The gateway mints a key when config has none. That is correct on a genuine first run
— and catastrophic when config was merely *wiped*, because the two are indistinguishable
from config alone: a corrupted/truncated ``config.yaml`` looks exactly like a fresh
install, so the daemon silently issues a new identity and every binding breaks at once.

So we keep a **mirror** of the key in the vault:

    config.yaml  = the source of truth (always wins when it has a key)
    vault        = a recovery mirror, consulted ONLY when config has nothing

With the mirror in place, a config wipe restores the *same* key — the tenant hash is
unchanged, so the webhook, the uplink and the Mini App button all stay valid and no
rotation happens at all. A deliberate rotation (``cloud key --rotate``, or the
weak-key upgrade) overwrites the mirror, so recovery can never resurrect a dead key.

Every operation here is best-effort: the vault may be absent or locked, and a boot
must never fail because the safety net is unavailable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VAULT_PROVIDER = "navig-deck"
_VAULT_LABEL = "NAVIG Deck API key (recovery mirror)"


def _vault():
    from navig.vault import get_vault

    return get_vault()


def backup(api_key: str) -> bool:
    """Mirror *api_key* into the vault so a config wipe can be undone.

    Called on every mint/upgrade AND opportunistically on a healthy boot, so existing
    installs (whose key predates this safety net) also become recoverable.
    """
    key = (api_key or "").strip()
    if not key:
        return False
    try:
        vault = _vault()
        if vault is None:
            return False
        data = {"api_key": key}
        existing = vault.get(VAULT_PROVIDER, caller="deck.key.backup")
        if existing is not None:
            if (existing.data or {}).get("api_key") == key:
                return True  # already mirrored — nothing to write
            vault.update(existing.id, data=data)
        else:
            vault.add(
                provider=VAULT_PROVIDER,
                credential_type="api_key",
                data=data,
                profile_id="default",
                label=_VAULT_LABEL,
            )
        return True
    except Exception:  # noqa: BLE001 — the mirror is a safety net, never a blocker
        logger.debug("deck.api_key vault backup skipped", exc_info=True)
        return False


def recover() -> str:
    """The mirrored key, or ``""`` when there is nothing to recover."""
    try:
        vault = _vault()
        if vault is None:
            return ""
        cred = vault.get(VAULT_PROVIDER, caller="deck.key.recover")
        if cred is None:
            return ""
        return str((cred.data or {}).get("api_key") or "").strip()
    except Exception:  # noqa: BLE001
        logger.debug("deck.api_key vault recovery skipped", exc_info=True)
        return ""


def is_mirrored() -> bool:
    """Whether a recovery mirror exists — i.e. whether the safety net is ARMED.

    Deliberately returns a bool, never the key: ``navig doctor`` needs to report that the
    net is in place, and a health check has no business handling the install's identity.
    """
    return bool(recover())


def note_restored(source: str) -> None:
    """Record that the install's identity had to be recovered from the vault.

    ONE place records it, because it happens on two paths (the gateway's boot and the
    CLI's ``ensure_in_config``) and it must look identical in ``navig doctor`` either
    way. A restore means config LOST the key — survivable, but never routine.
    """
    from navig.core import incidents

    incidents.record(incidents.DECK_KEY_RESTORED, source=source)


def _config_shows_prior_key(get) -> bool:
    """True when config still holds state that could ONLY exist if a deck.api_key existed
    BEFORE now — so a fresh 'generated' mint means the key was WIPED, not never-created.

    ``get`` is a dotted-key config accessor (``Config().get`` / ``cfg.get``).

    The signals are chosen to be strictly DOWNSTREAM of the key, so a genuine first run
    never trips them — even one whose onboarding set a bot token before the first mint:

      * ``telegram.webhook_url`` is literally ``<edge>/tg/sha256(deck.api_key)`` — it
        cannot exist without a prior key.
      * lighthouse mode / ``cloud.lighthouse_url`` requires the uplink the key
        authenticates.

    ``telegram.allowed_users`` is deliberately NOT used: it can be set during onboarding
    BEFORE the gateway's first mint, so it would false-positive on a real first run.
    """
    try:
        if str(get("telegram.webhook_url") or "").strip():
            return True
        if str(get("cloud.mode") or "").strip().lower() == "lighthouse":
            return True
        if str(get("cloud.lighthouse_url") or "").strip():
            return True
    except Exception:  # noqa: BLE001 — an observation must never break a boot
        return False
    return False


def note_generated(get, *, source: str) -> None:
    """A ``resolve()`` returned ``"generated"`` and the caller just minted a fresh key.

    ``"generated"`` assumes a genuine first run. But it is ALSO reached when config was
    wiped and the vault mirror was **not** available to restore the real key — in which
    case minting here silently RE-IDENTIFIES a live install: sha256(key) is the Lighthouse
    tenant, so the bot's mailbox moves, the Mini App button and every ingest URL point at a
    dead identity, and (until the webhook self-heals) the bot goes deaf. That is exactly
    the trigger that has been invisible.

    If config still shows a prior key existed (:func:`_config_shows_prior_key`), record it
    so ``navig doctor`` → Config Health surfaces the wipe. On a genuine first run this is a
    no-op. Best-effort throughout — a health note must never break the boot it observes.
    """
    try:
        if not _config_shows_prior_key(get):
            return
        from navig.core import incidents

        incidents.record(incidents.DECK_KEY_REIDENTIFIED, source=source)
        logger.warning(
            "Minted a NEW deck.api_key, but config still holds a webhook/lighthouse URL "
            "derived from an OLD one — this install was RE-IDENTIFIED (its Lighthouse "
            "tenant moved). The vault recovery mirror was NOT available to restore the "
            "real key. The webhook self-heals to the new key on channel start, but the "
            "Mini App button and any Signals/SMS ingest URLs will not. Investigate the "
            "config wipe and confirm the vault mirror is armed (navig doctor → Config "
            "Health)."
        )
    except Exception:  # noqa: BLE001
        logger.debug("deck.api_key re-identification note skipped", exc_info=True)


def resolve(config_key: str, *, min_len: int) -> tuple[str, str]:
    """Decide the install's key. Returns ``(key, reason)``.

    ``reason`` is one of:
      ``"ok"``        — config's key is healthy; use it (and re-mirror it).
      ``"restored"``  — config had NO key but the vault did: config was wiped, not
                        fresh. Restore the SAME key so no binding moves.
      ``"generated"`` — no key anywhere: a genuine first run. Mint one.
      ``"upgraded"``  — config's key is too weak to be safe. Deliberately rotate.

    Config always wins when it holds a key — the vault is a recovery mirror, never an
    override. That ordering is what keeps an intentional rotation from being silently
    reverted by a stale mirror on the next boot.
    """
    import secrets as _secrets

    key = (config_key or "").strip()
    if key and len(key) >= min_len:
        return key, "ok"
    if key:  # present but too short to be safe → a deliberate, warned rotation
        return "navig_" + _secrets.token_urlsafe(32), "upgraded"

    mirrored = recover()
    if mirrored:
        return mirrored, "restored"
    return "navig_" + _secrets.token_urlsafe(32), "generated"


def ensure_in_config(cfg, *, min_len: int = 16) -> str:
    """The CLI-side counterpart of the gateway's boot path: return the install's key,
    persisting + mirroring it if config had none.

    Every place that used to mint a key when ``deck.api_key`` was empty must go through
    here instead — ``navig cloud connect`` and ``navig lighthouse deploy`` each had their
    own private ``_ensure_api_key``, so a wiped config would have been re-identified by
    whichever one you happened to run first, exactly like the gateway did.
    """
    key, reason = resolve(str(cfg.get("deck.api_key") or ""), min_len=min_len)
    if reason == "ok":
        backup(key)  # opportunistic: make pre-existing installs recoverable too
        return key

    cfg.set("deck.api_key", key, scope="global")
    cfg.save(scope="global")
    if reason == "restored":
        logger.warning(
            "deck.api_key was missing from config but a recovery mirror exists — "
            "restoring the SAME key rather than minting a new identity."
        )
        note_restored("cli")
    else:
        backup(key)
        if reason == "generated":
            note_generated(cfg.get, source="cli")
    return key
