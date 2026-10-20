"""
navig decider — the decision model (Cloudflare Clef) that answers bounded questions.

Clef reads a message or document and answers fixed questions — yes/no, one of named
options, a level on a scale — with a probability per option, in ~40-200 ms. navig uses it,
when you switch it on, for decisions it used to ask a full LLM or a keyword list:

  telegram_triage   how complex is this Telegram message (picks the model tier)
  inbox_sort        which wiki / hub section a dropped file belongs in
  inbox_space       which of your spaces a file belongs in

It runs on YOUR Cloudflare account (the one connected for Lighthouse), off by default.
Whenever it is off, unsure or unavailable, navig decides exactly as it did before.

Commands:
  navig decider                      status (same as `status`)
  navig decider status [--json]      switches, credential, today's usage, last call
  navig decider on  [--use <id>]     switch on (everything, or one use)
  navig decider off [--use <id>]     switch off (everything, or one use)
  navig decider use clef-flash|clef  pick the model
  navig decider shadow on|off [--use <id>]
                                     compare with the old logic, keep the old logic deciding
  navig decider review               recent disagreements recorded in shadow mode
  navig decider test "<text>" …      one live call, answers printed as a table
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from navig import console_helper as ch

decider_app = typer.Typer(
    name="decider",
    help="Decision model (Cloudflare Clef) for fast bounded decisions — on/off, model, test.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@decider_app.callback(invoke_without_command=True)
def decider_default(ctx: typer.Context) -> None:
    """Show the decision model's status (default sub-command)."""
    if ctx.invoked_subcommand is not None:
        return
    _render_status(as_json=False)


# ── helpers ──────────────────────────────────────────────────


def _set(key: str, value: Any) -> None:
    from navig.config import get_config_manager

    get_config_manager().set_global(key, value)


def _match_uses(name: str) -> list[str]:
    """Exact use id, or every use starting with *name* (``inbox`` → both inbox uses)."""
    from navig.agent.decide import USES

    name = name.strip().lower().replace("-", "_")
    if name in USES:
        return [name]
    hits = [uid for uid in USES if uid.startswith(name)]
    if not hits:
        ch.error(f"Unknown use '{name}'. Known: {', '.join(USES)}")
        raise typer.Exit(1)
    return hits


def _credential_label(kind: str) -> str:
    return {
        "oauth": "[green]● Lighthouse login (OAuth)[/green]",
        "token": "[green]● Cloudflare API token (vault)[/green]",
        "env": "[green]● CLOUDFLARE_API_TOKEN[/green]",
    }.get(kind, "[red]✗ none — run navig lighthouse login[/red]")


def _agreement_cell(a: dict[str, int]) -> str:
    """``93% of 120`` — agreements over the comparisons where Clef answered."""
    answered = a.get("total", 0) - a.get("abstain", 0)
    if not a.get("total"):
        return "[dim]—[/dim]"
    if answered <= 0:
        return f"[dim]abstained ×{a['total']}[/dim]"
    pct = 100 * a.get("agree", 0) / answered
    colour = "green" if pct >= 90 else "yellow" if pct >= 75 else "red"
    return f"[{colour}]{pct:.0f}%[/{colour}] of {answered}"


def _render_status(*, as_json: bool) -> None:
    from navig.agent import decide

    st = decide.status()
    if as_json:
        typer.echo(json.dumps(st, indent=2, default=str))
        return

    from navig.console_helper import Table

    state = "[green]● on[/green]" if st["enabled"] else "[dim]○ off[/dim]"
    shadow = "  ·  [yellow]shadow mode — logged, not applied[/yellow]" if st["shadow"] else ""
    ch.console.print(f"[bold]Decision model (Clef)[/bold]  {state}  ·  model [cyan]{st['model']}[/cyan]{shadow}")

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Use", no_wrap=True)
    table.add_column("Mode", no_wrap=True)
    table.add_column("Today", no_wrap=True, justify="right")
    table.add_column("Agrees (7d)", no_wrap=True, justify="right")
    table.add_column("What it decides")
    for u in st["uses"]:
        today = st["usage_today"].get(u["id"], {})
        calls = today.get("calls", 0)
        failed = today.get("failed", 0)
        mode = u.get("mode", "on" if u["on"] else "off")
        if mode == "off":
            switch = "[dim]○ off[/dim]"
        elif not st["enabled"]:
            switch = f"[dim]● {mode} (master off)[/dim]"
        elif mode == "shadow" or st["shadow"]:
            switch = "[yellow]◐ shadow[/yellow]"
        else:
            switch = "[green]● on[/green]"
        count = f"{calls}" + (f" [red]({failed} failed)[/red]" if failed else "")
        table.add_row(u["id"], switch, count, _agreement_cell(u.get("agreement_7d") or {}), u["description"])
    ch.console.print(table)

    ch.console.print(f"  Credential  {_credential_label(st['credential'])}")
    if st["credential"] and not st["account_known"]:
        ch.console.print("  Account     [yellow]looked up on the first call[/yellow]")
    cap = st["daily_cap"] or "no cap"
    ch.console.print(f"  Today       {st['calls_today']} calls  ·  cap {cap}  (UTC day)")
    last = st["last_call"]
    if last is None:
        ch.console.print("  Last call   [dim]never — try: navig decider test \"some text\"[/dim]")
    elif last["ok"]:
        ch.console.print(
            f"  Last call   [green]✓[/green] {last['at']}  {last['latency_ms']} ms  "
            f"{last['model']}  [dim]({last['use']})[/dim]"
        )
    else:
        ch.console.print(
            f"  Last call   [red]✗ {last['kind']}[/red] {last['at']}  [dim]{last['detail']}[/dim]"
        )

    if not st["credential"]:
        ch.dim("Next: connect Cloudflare with `navig lighthouse login`, then `navig decider test`.")
    elif last is not None and not last["ok"] and last["kind"] == "auth":
        ch.dim(f"Next: {decide.grant_hint()}")
    elif not st["enabled"]:
        ch.dim("Next: `navig decider test \"…\"` to try it, then `navig decider on`.")
    else:
        ch.dim("Switch one use off: navig decider off --use <id>  ·  everything: navig decider off")


# ── commands ─────────────────────────────────────────────────


@decider_app.command("status")
def decider_status(
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Switches, credential, today's usage and the last call."""
    _render_status(as_json=json_output)


@decider_app.command("on")
def decider_on(
    use: Annotated[
        str | None,
        typer.Option("--use", "--only", help="Switch on just this use (id or prefix, e.g. inbox)."),
    ] = None,
) -> None:
    """Switch the decision model on — for everything, or for one use."""
    if use:
        for uid in _match_uses(use):
            _set(f"decide.use.{uid}", True)
            ch.success(f"{uid}: on")
    _set("decide.enabled", True)
    ch.success("Decision model on.")
    from navig.agent import decide

    if not decide.status()["credential"]:
        ch.warning("No Cloudflare credential yet — decisions use the previous method until you run `navig lighthouse login`.")


@decider_app.command("off")
def decider_off(
    use: Annotated[
        str | None,
        typer.Option("--use", "--only", help="Switch off just this use (id or prefix, e.g. telegram)."),
    ] = None,
) -> None:
    """Switch the decision model off — everywhere, or for one use."""
    if use:
        for uid in _match_uses(use):
            _set(f"decide.use.{uid}", False)
            ch.success(f"{uid}: off — it decides the previous way again.")
        return
    _set("decide.enabled", False)
    ch.success("Decision model off — every decision uses the previous method.")


@decider_app.command("use")
def decider_use(
    model: Annotated[str, typer.Argument(help="clef-flash (fast, ~40 ms) or clef (stronger, ~200 ms).")],
) -> None:
    """Pick the model."""
    from navig.agent.decide import MODELS

    model = model.strip().lower()
    if model not in MODELS:
        ch.error(f"Unknown model '{model}'. Choose one of: {', '.join(MODELS)}")
        raise typer.Exit(1)
    _set("decide.model", model)
    ch.success(f"Model: {model}")


@decider_app.command("shadow")
def decider_shadow(
    state: Annotated[str, typer.Argument(help="on = compare and record only; off = apply answers.")],
    use: Annotated[
        str | None,
        typer.Option("--use", "--only", help="Just this use (id or prefix); default: every use."),
    ] = None,
) -> None:
    """Shadow mode: Clef answers are compared with the old logic and recorded, not applied.

    Watch the agreement in `navig decider status`, read the disagreements with
    `navig decider review`, then `navig decider shadow off --use <id>` to let Clef decide.
    """
    state = state.strip().lower()
    if state not in ("on", "off"):
        ch.error("Use: navig decider shadow on|off [--use <id>]")
        raise typer.Exit(1)
    if use:
        for uid in _match_uses(use):
            _set(f"decide.use.{uid}", "shadow" if state == "on" else "on")
            ch.success(f"{uid}: {'shadow — compared and recorded, not applied' if state == 'on' else 'on — Clef decides'}")
        return
    _set("decide.shadow", state == "on")
    ch.success("Shadow mode on for every use — answers are recorded, not applied." if state == "on" else "Shadow mode off — each use follows its own mode.")


@decider_app.command("review")
def decider_review(
    use: Annotated[str | None, typer.Option("--use", help="Only this use (exact id).")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many to show.")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """The most recent shadow-mode DISAGREEMENTS — what to read before switching a use on."""
    from navig.store.decide_usage import get_decide_usage_store

    rows = get_decide_usage_store().recent_disagreements(limit=max(1, limit), use=use)
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        ch.dim("No disagreements recorded. Put a use in shadow mode: navig decider shadow on --use <id>")
        return
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("When", no_wrap=True)
    table.add_column("Use · question", no_wrap=True)
    table.add_column("Clef", no_wrap=True)
    table.add_column("Previous", no_wrap=True)
    table.add_column("Sure", no_wrap=True, justify="right")
    table.add_column("Text (redacted)")
    for r in rows:
        table.add_row(
            str(r["at"])[:16].replace("T", " "),
            f"{r['use']} · {r['question']}",
            f"[cyan]{r['clef']}[/cyan]",
            str(r["legacy"]),
            f"{float(r['confidence']):.0%}",
            str(r["sample"] or ""),
        )
    ch.console.print(table)
    ch.dim("Where Clef was right, its answer is safe to apply: navig decider shadow off --use <id>")


def _split_spec(spec: str, flag: str) -> tuple[str, str]:
    qid, sep, rest = spec.partition("=")
    if not sep or not qid.strip() or not rest.strip():
        ch.error(f"{flag} expects id=…, got '{spec}'")
        raise typer.Exit(1)
    return qid.strip(), rest.strip()


def _image_payload(path: Path) -> dict[str, str]:
    import base64

    kinds = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    ctype = kinds.get(path.suffix.lower())
    if ctype is None:
        ch.error(f"{path.name}: images must be PNG, JPEG or WebP")
        raise typer.Exit(1)
    data = path.read_bytes()
    if len(data) > 4 * 1024 * 1024:
        ch.error(f"{path.name}: images must be 4 MiB or smaller")
        raise typer.Exit(1)
    return {"content_type": ctype, "base64": base64.b64encode(data).decode("ascii")}


@decider_app.command("test")
def decider_test(
    text: Annotated[str, typer.Argument(help="The message or document text to decide about.")],
    yesno: Annotated[
        list[str] | None,
        typer.Option("--yesno", help="id=question  (repeatable)"),
    ] = None,
    choice: Annotated[
        list[str] | None,
        typer.Option("--choice", help="id=option1,option2,…  (repeatable)"),
    ] = None,
    score: Annotated[
        list[str] | None,
        typer.Option("--score", help="id=lowest,…,highest  (repeatable)"),
    ] = None,
    image: Annotated[
        list[Path] | None,
        typer.Option("--image", help="PNG/JPEG/WebP to include (up to 4)."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="clef-flash or clef (default: configured).")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """One live call — works even while the decider is switched off.

    Without questions it asks a demo pair: is this urgent (yes/no) and how important is it.
    """
    from navig.agent import decide
    from navig.platform.paths import resolve_user_path

    questions: list[decide.Question] = []
    try:
        for spec in yesno or []:
            qid, instr = _split_spec(spec, "--yesno")
            questions.append(decide.yesno(qid, instr))
        for spec in choice or []:
            qid, opts = _split_spec(spec, "--choice")
            names = [o.strip() for o in opts.split(",") if o.strip()]
            questions.append(decide.choice(qid, f"Which {qid} fits best?", names))
        for spec in score or []:
            qid, levels = _split_spec(spec, "--score")
            lv = [x.strip() for x in levels.split(",") if x.strip()]
            questions.append(decide.score(qid, f"Rate the {qid}.", lv))
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    if not questions:
        questions = [
            decide.yesno("urgent", "Does this need attention right away?"),
            decide.score("importance", "How important is this?", ["trivial", "low", "medium", "high", "critical"]),
        ]
    images = [_image_payload(resolve_user_path(p)) for p in (image or [])][:4]

    try:
        result = decide.call(text, questions, model=model, images=images or None)
    except decide.DecideError as exc:
        if json_output:
            typer.echo(json.dumps({"ok": False, "kind": exc.kind, "error": str(exc)}, indent=2))
        else:
            ch.error(f"{exc.kind}: {exc}")
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "answers": {
                        k: {"answer": d.answer, "confidence": d.confidence, "probs": d.probs, "value": d.value}
                        for k, d in result.answers.items()
                    },
                },
                indent=2,
            )
        )
        return

    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Question", no_wrap=True)
    table.add_column("Answer", no_wrap=True)
    table.add_column("Sure", no_wrap=True, justify="right")
    table.add_column("Probabilities")
    for q in questions:
        d = result.answers[q.id]
        tail = "  ".join(f"{opt} {p:.2f}" for opt, p in d.ranked()[:4])
        if d.value is not None:
            tail += f"   [dim](score {d.value:.2f})[/dim]"
        table.add_row(q.id, f"[bold]{d.answer}[/bold]", f"{d.confidence:.0%}", tail)
    ch.console.print(table)
    ch.dim(f"{result.model} · {result.latency_ms} ms · {result.input_tokens} input tokens")
