"""Next steps for a file an agent just produced or received.

A closed, declarative table: artifact kind → candidate steps. Every step names an exact
installed command path and an argument template filled ONLY from the file path; the caller
never supplies a command string. A step is offered when its command is installed; a step
that publishes is offered once per *connected* account and listed under ``unlocks`` (with
the command that connects it) when no account is connected.

Every path here must exist in the command manifest — ``tests/capabilities`` checks the
table against ``core/generated/commands.json`` so a renamed command fails the build
instead of producing a suggestion that cannot run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS: dict[str, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"}),
    "video": frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}),
    "audio": frozenset({".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus"}),
    # A fan-out brief: JSON {title, body, url, image?} — what `navig social fan-out` reads.
    "brief": frozenset({".json"}),
}  # fmt: skip


@dataclass(frozen=True)
class Step:
    id: str
    label: str
    path: str
    args: tuple[str, ...] = ("{file}",)
    #: social providers whose connected accounts unlock this step (one suggestion each)
    providers: tuple[str, ...] = ()
    #: per-account argument template; ``{account}`` is the account id
    account_args: tuple[str, ...] = ()
    #: the command an operator runs to connect a missing provider
    connect: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


STEPS: dict[str, tuple[Step, ...]] = {
    "video": (
        Step("inspect", "Read duration, resolution and fps", "navig generate probe"),
        Step("loudness", "Measure loudness against platform targets", "navig audio delivery",
             args=("{file}", "--json")),
        Step("clean_dialogue", "Clean the dialogue and keep it in sync", "navig audio clean-video",
             args=("{file}", "--json")),
        Step("score", "Compose new music that follows the cuts (replaces the bed)",
             "navig audio score", args=("{file}", "--dry-run", "--json"),
             notes=("Runs as a dry run first; drop --dry-run to render.",)),
        Step("keyframes", "Extract keyframes for a thumbnail or review", "navig generate frames"),
        Step("briefing", "Write a briefing (frames + transcript) of the video",
             "navig generate analyse"),
        Step("review_card", "Send it to your Telegram for review before posting",
             "navig social card", args=("--caption", "<caption>", "--json"),
             notes=("Telegram review cards carry text and photos; attach a keyframe.",)),
        Step("post_youtube", "Upload to YouTube (dry run first)", "navig social upload",
             args=("{file}", "--dry-run", "--json"), providers=("youtube",),
             account_args=("{file}", "--account", "{account}", "--dry-run", "--json"),
             connect="navig social connect youtube"),
    ),
    "image": (
        Step("ingest", "Add it to the space's media library for review", "navig generate ingest"),
        Step("palette", "Extract its colour palette", "navig generate palette"),
        Step("review_card", "Send it to your Telegram for review before posting",
             "navig social card", args=("--photo", "{file}", "--caption", "<caption>", "--json")),
        Step("fan_out", "Publish to your connected networks (dry run first)",
             "navig social fan-out", args=("--file", "<brief.json>", "--dry-run", "--json"),
             providers=("facebook", "instagram", "threads", "linkedin", "pinterest"),
             account_args=("--file", "<brief.json>", "--to", "{provider}", "--dry-run", "--json"),
             connect="navig social connect <network>",
             notes=("fan-out reads a JSON brief {title, body, url, image}; set image to this file.",)),
    ),
    "audio": (
        Step("loudness", "Measure integrated loudness and true peak", "navig audio loudness"),
        Step("delivery", "Check (and fix) it against a platform's delivery spec",
             "navig audio delivery", args=("{file}", "--json")),
        Step("isolate", "Remove background noise, keep the voice", "navig audio isolate",
             args=("{file}", "--json")),
        Step("enhance", "Restore and master the recording", "navig audio enhance",
             args=("{file}", "--json")),
        Step("stems", "Split into parts (vocals, drums, …)", "navig audio stems",
             args=("{file}", "--json")),
    ),
    "brief": (
        Step("fan_out", "Publish this brief to your connected networks (dry run first)",
             "navig social fan-out", args=("--file", "{file}", "--dry-run", "--json"),
             providers=("facebook", "instagram", "threads", "linkedin", "pinterest", "devto"),
             account_args=("--file", "{file}", "--to", "{provider}", "--dry-run", "--json"),
             connect="navig social connect <network>"),
    ),
}  # fmt: skip


def kind_of(path: str) -> str | None:
    suffix = Path(str(path)).suffix.lower()
    for kind, suffixes in KINDS.items():
        if suffix in suffixes:
            return kind
    return None


def _fill(template: tuple[str, ...], **values: str) -> list[str]:
    out = []
    for part in template:
        for key, value in values.items():
            part = part.replace("{" + key + "}", value)
        out.append(part)
    return out


def _connected(probe_results: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """provider -> connected accounts, across every probe."""
    out: dict[str, list[dict[str, Any]]] = {}
    for result in probe_results.values():
        for account in result.get("accounts") or []:
            if account.get("state") in ("connected", "expires"):
                out.setdefault(account["provider"], []).append(account)
    return out


def suggest(
    file: str,
    *,
    installed: set[str] | None = None,
    probe_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ranked next steps for *file* using only what is installed and connected."""
    from navig.capabilities.safety import classify

    kind = kind_of(file)
    if kind is None:
        return {
            "file": file,
            "kind": None,
            "steps": [],
            "unlocks": [],
            "hint": "Unknown file type. Use navig_capabilities action=find with your goal.",
        }
    if installed is None:
        from navig.capabilities.registry import commands

        installed = {row["path"] for row in commands()}
    if probe_results is None:
        from navig.capabilities.probes import run_probes

        probe_results = run_probes()
    connected = _connected(probe_results)

    steps: list[dict[str, Any]] = []
    unlocks: list[dict[str, Any]] = []
    for step in STEPS[kind]:
        if step.path not in installed:
            continue
        base = {
            "id": step.id,
            "label": step.label,
            "path": step.path,
            "safety": classify(step.path),
            "notes": list(step.notes),
        }
        if not step.providers:
            steps.append({**base, "args": _fill(step.args, file=file)})
            continue
        accounts = [(p, a) for p in step.providers for a in connected.get(p, [])]
        if not accounts:
            unlocks.append(
                {
                    "id": step.id,
                    "label": step.label,
                    "needs": list(step.providers),
                    "connect": step.connect,
                }
            )
            continue
        for provider, account in accounts:
            label = account.get("label") or provider
            steps.append(
                {
                    **base,
                    "id": f"{step.id}:{provider}",
                    "label": f"{step.label} — {provider} {label}".rstrip(),
                    "account": {"provider": provider, "label": label},
                    "args": _fill(
                        step.account_args or step.args,
                        file=file,
                        provider=provider,
                        account=str(account.get("id") or account.get("label") or ""),
                    ),
                }
            )
    return {
        "file": file,
        "kind": kind,
        "steps": steps,
        "unlocks": unlocks,
        "hint": (
            "Offer these to the operator as a short plan before acting. Run one with "
            "navig_run action=run path=<path> args=<args>; publishing steps need approval."
        ),
    }
