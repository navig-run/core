"""Sample notifications for the deck "Test" buttons — fire one representative
event per monitor/producer so you can prove the whole pipe (type → matrix →
deck/Telegram) without waiting for a real crash, outage, or camera access.

Test notifications are clearly labelled ``[Test]`` and carry ``data._test=True``.
"""

from __future__ import annotations

from typing import Any

# monitor key → (notify_type, title, body, priority)
_SAMPLES: dict[str, tuple[str, str, str, str]] = {
    "self_errors": ("self_error", "[Test] NAVIG error", "A sample self-error notification.", "high"),
    # Dispatches the SAME type ConfigIncidentReporter uses ("config_incident"), so pressing
    # Test exercises the real config-rescue → deck/telegram matrix. Without this entry the
    # default-ON "Config rescues" monitor's Test button 400s "no sample for monitor".
    "config_incidents": ("config_incident", "[Test] NAVIG config health",
                         "A sample config-rescue notification (e.g. a wiped config restored, or a re-identified deck key).", "high"),
    "connectivity": ("connectivity", "[Test] Brain reachability", "A sample connectivity notification.", "normal"),
    "resources": ("system_alert", "[Test] Resource alert", "A sample disk/CPU/memory alert.", "normal"),
    # The same notify TYPE the host-check monitor reaches through the system-event
    # bridge (host_disk_warning → system_alert), so Test proves the routing half
    # without waiting for a real threshold crossing on a configured host.
    "host_checks": ("system_alert", "[Test] Host health check",
                    "A sample per-host disk/memory/service alert (with its paired all-clear).", "normal"),
    "webcam": ("webcam_on", "[Test] Webcam in use", "A sample webcam-in-use notification.", "high"),
    # Same type the reaper really dispatches (browser_reaper.NOTIFY_TYPE), so pressing Test
    # exercises the real routing rather than a made-up type. Not `config_incident`: that one
    # means the config/identity layer rescued itself, and a browser cleanup filed there
    # would dilute the signal that the bot is about to go deaf.
    "browser_reaper": ("system_alert", "[Test] Automation browser cleanup",
                       "A sample notification for closing an idle automation browser.", "low"),
    # The escalation's own trigger is a Pushover emergency, so Test sends one through the
    # type that defaults to Pushover after setup (security_alert, critical). Leaving it
    # unacknowledged for the escalation window proves the SMS step end to end.
    # The type the update check and the agent-proposal path really dispatch.
    "lens_updates": ("lens_review", "[Test] Lens update to review",
                     "A sample Lens update waiting for your approval.", "normal"),
    "pushover_escalation": ("security_alert", "[Test] Pushover emergency",
                            "A sample emergency — leave it unacknowledged to see the SMS escalation.",
                            "critical"),
    # Same type the breach monitor really dispatches, so Test exercises the real
    # routing without waiting for a (hopefully imaginary) leak.
    "breach": ("breach_alert", "[Test] Breach found",
               "A sample breach alert — alice@example.com in 2 dumps, password a******9. Full list: navig vault breach list",
               "high"),
    # Same notify TYPE the site-probe monitor reaches through the system-event
    # bridge (site_down → system_alert), so Test proves the routing half without
    # waiting for a real outage on a configured URL.
    # The monitor itself is silent; what reaches the operator is the incident it records
    # after repeated failed passes, pushed by the config-incident reporter — so Test
    # exercises that same type.
    "recall_ingest": ("config_incident", "[Test] Recall reading",
                      "A sample notice that Recall's background reading failed several passes in a row.",
                      "normal"),
    "site_probes": ("system_alert", "[Test] Site uptime & certs",
                    "A sample site-down / certificate-expiry alert (with its paired all-clear).", "normal"),
}


def monitor_sample(name: str) -> tuple[str, str, str, str] | None:
    return _SAMPLES.get(name)


async def dispatch_monitor_test(name: str) -> dict[str, Any] | None:
    """Fire the sample notification for *name*; returns the dispatch result."""
    sample = _SAMPLES.get(name)
    if sample is None:
        return None
    notify_type, title, body, priority = sample
    from navig.notify import dispatch

    return await dispatch(
        notify_type, title, body, priority=priority, data={"_test": True, "monitor": name}
    )
