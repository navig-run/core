"""
NAVIG Provider Verifier

Runs a multi-layer validity check on every registered provider and surfaces
failures explicitly.  Called by:

  - ``navig providers verify`` CLI command (commands directory layout)
  - Integration tests (tests/providers/test_registry.py)
  - Telegram ``/providers`` handler (status badges)

Usage::

    from navig.providers.verifier import verify_all_providers, verify_provider

    results = verify_all_providers()
    for r in results:
        if not r.ok:
            print(f"❌ {r.display_name}: {', '.join(r.issues)}")
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field

from loguru import logger

from .registry import ALL_PROVIDERS, ProviderManifest


@dataclass
class ProviderVerificationResult:
    """
    Outcome of verifying a single provider.

    Attributes
    ----------
    id              Provider ID from ``ProviderManifest.id``.
    display_name    Human-readable name for error messages.
    manifest_ok     Entry exists in ``ALL_PROVIDERS`` with valid required fields.
    factory_ok      ``create_provider(id)`` would not raise ``ValueError`` (ID is
                    present in ``_PROVIDER_MAP``).  Disabled-by-default providers
                    that are not yet in the factory map are flagged but not hard-failed.
    config_ok       ``builtin_provider_configs()`` returns a matching ``ProviderConfig``.
                    ``True`` for local/proxy providers that don't need a config entry.
    key_detected    For ``requires_key=True`` providers: a key is present in env or
                    vault.  Always ``True`` for local/proxy providers.
    local_probe_ok  For ``local_probe`` providers only: the probe host:port is reachable.
                    ``None`` if no probe is configured.
    issues          List of named, actionable error strings.  Empty = all OK.
    ok              Convenience: ``True`` when ``issues`` is empty.
    """

    id: str
    display_name: str
    manifest_ok: bool = True
    factory_ok: bool = True
    config_ok: bool = True
    key_detected: bool = True
    local_probe_ok: bool | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0


def _check_factory(provider_id: str) -> bool:
    """Return True if ``create_provider(provider_id)`` would succeed (key present in map)."""
    try:
        from navig.agent.llm_providers import _PROVIDER_MAP

        ids_to_check = {provider_id, provider_id.replace("_", "").replace("-", "")}
        for key in _PROVIDER_MAP:
            if key in ids_to_check or key.replace("_", "").replace("-", "") in ids_to_check:
                return True
        # Also accept exact match on class .name attribute
        for cls in _PROVIDER_MAP.values():
            if getattr(cls, "name", "") == provider_id:
                return True
        return False
    except ImportError:
        return False


def _check_config(provider_id: str) -> bool:
    """Return True if BUILTIN_PROVIDERS has an entry for this provider."""
    try:
        from navig.providers.types import BUILTIN_PROVIDERS

        return provider_id in BUILTIN_PROVIDERS
    except ImportError:
        return False


def _check_key(manifest: ProviderManifest) -> bool:
    """
    Detect whether a usable credential is available for *manifest*.

    Order:
      1. Check environment variables listed in ``manifest.env_vars``.
      2. Check vault paths listed in ``manifest.vault_keys``.
    """
    for var in manifest.env_vars:
        if os.environ.get(var, "").strip():
            return True
    try:
        from navig.vault import get_vault_v2

        vault = get_vault_v2()
        if vault is not None:
            for vk in manifest.vault_keys:
                try:
                    raw = vault.get_secret(vk)
                    if raw:
                        return True
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return False


def _check_probe(probe: str) -> bool:
    """TCP-connect to *probe* (``host:port``) with a 500ms timeout."""
    try:
        host, port_str = probe.rsplit(":", 1)
        port = int(port_str)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        reachable = sock.connect_ex((host, port)) == 0
        sock.close()
        return reachable
    except Exception:
        return False


def verify_provider(manifest: ProviderManifest) -> ProviderVerificationResult:
    """
    Run all validity checks for *manifest* and return a ``ProviderVerificationResult``.

    This function is intentionally non-raising — every failure is captured as
    a named issue string so callers can report them uniformly.
    """
    issues: list[str] = []

    # 1. Manifest validity (always true if we got here, but guard required fields)
    manifest_ok = bool(manifest.id and manifest.display_name and manifest.tier)
    if not manifest_ok:
        issues.append("manifest missing required fields (id / display_name / tier)")

    # 2. Factory check
    factory_ok = _check_factory(manifest.id)
    # Disabled providers that aren't in the factory yet get a warning, not an error
    if not factory_ok and manifest.enabled:
        issues.append(
            f"not in runtime factory (_PROVIDER_MAP) — "
            f"add '{manifest.id}' to navig/agent/llm_providers.py::_PROVIDER_MAP"
        )
    elif not factory_ok and not manifest.enabled:
        # Log at debug; not an error for opt-in providers
        logger.debug("Provider '{}' is disabled and not in factory (expected)", manifest.id)

    # 3. Config check (local/proxy providers are excluded — they don't need a ProviderConfig)
    config_ok = True
    if manifest.tier == "cloud":
        config_ok = _check_config(manifest.id)
        if not config_ok:
            issues.append(
                f"no ProviderConfig entry in BUILTIN_PROVIDERS — "
                f"add '{manifest.id}' to navig/providers/types.py::BUILTIN_PROVIDERS"
            )

    # 4. Key detection (skip for local/proxy or requires_key=False providers)
    key_detected = True
    if manifest.requires_key and manifest.tier != "local":
        key_detected = _check_key(manifest)
        if not key_detected and manifest.enabled:
            env_hint = " or ".join(manifest.env_vars) or "(no env var defined)"
            vault_hint = manifest.vault_keys[0] if manifest.vault_keys else "(no vault key defined)"
            issues.append(
                f"no API key found — set {env_hint} or store in vault under '{vault_hint}'"
            )

    # 5. Local probe (only for providers that declare one)
    local_probe_ok: bool | None = None
    if manifest.local_probe:
        local_probe_ok = _check_probe(manifest.local_probe)
        if not local_probe_ok and manifest.tier == "local":
            issues.append(
                f"local service unreachable at {manifest.local_probe} — "
                f"is {manifest.display_name} running?"
            )

    result = ProviderVerificationResult(
        id=manifest.id,
        display_name=manifest.display_name,
        manifest_ok=manifest_ok,
        factory_ok=factory_ok,
        config_ok=config_ok,
        key_detected=key_detected,
        local_probe_ok=local_probe_ok,
        issues=issues,
    )

    if issues:
        for issue in issues:
            logger.warning("Provider '{}': {}", manifest.id, issue)

    return result


def verify_all_providers(
    include_disabled: bool = False,
) -> list[ProviderVerificationResult]:
    """
    Verify every provider in ``ALL_PROVIDERS``.

    Parameters
    ----------
    include_disabled
        When ``True``, runs checks against disabled (opt-in) providers too.
        Defaults to ``False``.

    Returns
    -------
    List of ``ProviderVerificationResult`` — one per provider checked.
    All failures are already logged as warnings by ``verify_provider``.
    """
    providers = ALL_PROVIDERS if include_disabled else [p for p in ALL_PROVIDERS if p.enabled]
    results: list[ProviderVerificationResult] = []
    for manifest in providers:
        try:
            result = verify_provider(manifest)
        except Exception as exc:
            logger.error("Unexpected error verifying provider '{}': {}", manifest.id, exc)
            result = ProviderVerificationResult(
                id=manifest.id,
                display_name=manifest.display_name,
                issues=[f"unexpected verification error: {exc}"],
            )
        results.append(result)

    failures = [r for r in results if not r.ok]
    if failures:
        logger.warning(
            "{} of {} providers have issues: {}",
            len(failures),
            len(results),
            ", ".join(r.id for r in failures),
        )
    else:
        logger.debug("All {} verified providers passed.", len(results))

    return results
