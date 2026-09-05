"""``navig block`` + ``navig apply`` — manage and run blocks.

- ``navig apply <id-or-spec>``  resolve → (install if needed, trust-gated) →
  collect typed inputs → execute under policy → verify → write a receipt.
- ``navig block list|show|new|verify``  author/inspect blocks locally.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch

block_app = typer.Typer(
    help="Author, inspect, and manage NAVIG blocks (installable, verifiable outcomes).",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# list / show / verify / new
# ---------------------------------------------------------------------------


@block_app.command("list")
def block_list(
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting."),
    as_json: bool = typer.Option(False, "--json", help="Emit the catalog as JSON."),
) -> None:
    """List installed/discovered blocks."""
    from navig.blocks import discover_blocks

    blocks = sorted(discover_blocks(), key=lambda x: x.id)
    if as_json:
        ch.raw_print(
            json.dumps(
                [
                    {
                        "id": b.id,
                        "version": b.version,
                        "category": b.category,
                        "verify": b.verify.kind,
                        "paid": bool(b.marketplace),
                        "description": b.description,
                    }
                    for b in blocks
                ],
                indent=2,
            )
        )
        return

    if not blocks:
        ch.info("No blocks installed. Try: navig install add block:navig-run/community/blocks/<id>")
        return

    if plain:
        for b in blocks:
            print(f"{b.id}\t{b.version}\t{b.category}\t{b.verify.kind}")
        return

    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Block", style="bold", no_wrap=True)
    table.add_column("Ver", no_wrap=True)
    table.add_column("Category", no_wrap=True)
    # `verify` is the whole promise of a Block — an outcome you can PROVE. A block that
    # verifies nothing is a checklist, so the distinction earns a colour, not a bare word.
    table.add_column("Verify", no_wrap=True)
    # Exactly one wrappable column, so a narrow terminal folds the prose instead of
    # truncating the ids with an ellipsis.
    table.add_column("What it does")

    verifiable = 0
    for b in blocks:
        if b.verify.kind == "none":
            verify = "[dim]○ none[/dim]"
        else:
            verifiable += 1
            verify = f"[green]● {b.verify.kind}[/green]"
        name = f"{b.id} [yellow]·paid[/yellow]" if b.marketplace else b.id
        table.add_row(name, b.version, b.category, verify, b.description or "[dim]—[/dim]")

    ch.console.print(table)
    ch.dim(
        f"  {len(blocks)} block(s) · {verifiable} with a verified outcome · "
        f"apply one with navig apply <id>"
    )


@block_app.command("show")
def block_show(block_id: str = typer.Argument(..., help="Block id")) -> None:
    """Show a block's spec: inputs, steps (with computed risk), and verify."""
    from navig.blocks import capability_risk, find_block

    b = find_block(block_id)
    if b is None:
        ch.warning(f"block '{block_id}' not found.")
        raise typer.Exit(1)
    ch.info(f"{b.name}  ({b.id} v{b.version})  \\[{b.category}] · {b.license}")
    if b.description:
        ch.dim(f"  {b.description}")
    ch.info(f"  digest: {b.digest}")
    _show_requirements(b)
    ch.info("  inputs:")
    for i in b.inputs:
        req = "required" if i.required else "optional"
        extra = f" (secret)" if i.is_secret else ""
        ch.info(f"    - {i.key}: {i.type} · {req}{extra}")
    ch.info("  steps:")
    for s in b.steps:
        risk = capability_risk(s.capabilities)
        ch.info(f"    - {s.id} \\[{s.kind}] risk={risk}")
    ch.info(f"  verify: {b.verify.kind} → {b.verify.level}")
    if b.marketplace:
        ch.info(f"  marketplace: {b.marketplace}")


def _show_requirements(b) -> None:
    """Print declared `requires:` (tools + plugins + detect probes) with a live
    met/unmet marker for tools/plugins, so you see what's needed before `navig apply`
    runs the block. `detect` probes are listed but NOT executed here (they run a
    command — use `navig block doctor` to actually check them)."""
    from navig.blocks.policy import check_requirements

    req = b.requires or {}
    # probe=False: `show` must never execute a detect probe (it runs a command).
    checks = check_requirements(req)
    detect = [d for d in (req.get("detect") or []) if isinstance(d, dict)]
    if not (checks or detect):
        return
    ch.info("  requires:")
    for c in checks:
        state = "✓ present" if c.ok else f"✗ {c.detail}"
        line = f"    - {c.kind} {c.name}: {state}"
        if not c.ok and c.fix:
            line += f"  → {c.fix}"
        ch.info(line)
    for d in detect:
        label = str(d.get("label") or (d.get("run") or ["probe"])[0])
        ch.info(f"    - detect {label}: (runtime probe — check with `navig block doctor {b.id}`)")


@block_app.command("doctor")
def block_doctor(
    block_id: str = typer.Argument(..., help="Block id"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Check whether a block can run **here**: declared tools/plugins present and its
    `detect` probes passing. Prints each requirement's status and how to fix it;
    exits 0 if everything is satisfied, 1 otherwise. This runs the block's `detect`
    probes (read-only availability checks) but never its steps."""
    from navig.blocks import find_block
    from navig.blocks.policy import check_requirements

    b = find_block(block_id)
    if b is None:
        ch.warning(
            f"block '{block_id}' not found. Install it: "
            f"navig install add block:navig-run/community/blocks/{block_id}")
        raise typer.Exit(1)

    # probe=True: doctor is the one display path that DOES run the detect probes —
    # that's its whole job ("can this run here?"). `block show` deliberately does not.
    checks = check_requirements(b.requires, probe=True)

    all_ok = all(c.ok for c in checks)
    if json_out:
        print(json.dumps({
            "block": b.id,
            "ok": all_ok,
            "checks": [{"kind": c.kind, "name": c.name, "ok": c.ok,
                        "detail": c.detail, "fix": c.fix} for c in checks],
        }, indent=2))
        raise typer.Exit(0 if all_ok else 1)

    if not checks:
        ch.success(f"{b.id}: no requirements declared — ready to `navig apply {b.id}`.")
        raise typer.Exit(0)
    ch.info(f"{b.name}  ({b.id}) — can this run here?")
    for c in checks:
        line = f"    {'✓' if c.ok else '✗'} {c.kind} {c.name}"
        if not c.ok and c.fix:
            line += f"  → {c.fix}"
        (ch.info if c.ok else ch.warning)(line)
    if all_ok:
        ch.success(f"All requirements satisfied — `navig apply {b.id}` will run.")
        raise typer.Exit(0)
    ch.error(
        f"Unmet requirements above. Fix them (details: `navig block show {b.id}`), "
        f"then re-run `navig block doctor {b.id}`.")
    raise typer.Exit(1)


@block_app.command("verify")
def block_verify(target: str = typer.Argument(..., help="Block id or path to a BLOCK.md / block dir")) -> None:
    """Lint a block manifest (does NOT execute it)."""
    from navig.blocks import find_block, validate_block
    from navig.blocks.loader import BlockError, lint_block, parse_block_file

    block = None
    p = Path(target)
    try:
        if p.exists():
            bf = p / "BLOCK.md" if p.is_dir() else p
            block = parse_block_file(bf)
        else:
            block = find_block(target)
    except BlockError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    if block is None:
        ch.warning(f"no block found at '{target}'.")
        raise typer.Exit(1)

    problems = validate_block(block)
    # If a BLOCK.sig sidecar exists, verify the manifest signature against the digest.
    sig_path = block.source_path.parent / "BLOCK.sig"
    if sig_path.exists():
        import json

        from navig.license.device_keys import verify_bytes

        try:
            sc = json.loads(sig_path.read_text(encoding="utf-8"))
            if sc.get("digest") != block.digest:
                problems.append("BLOCK.sig digest does not match current manifest (edited after signing)")
            elif not verify_bytes(block.digest.encode("utf-8"), sc.get("signature", ""), sc.get("signer_pubkey", "")):
                problems.append("BLOCK.sig signature is invalid")
            else:
                ch.dim("  signed: device signature verified ✓")
        except (OSError, ValueError):
            problems.append("BLOCK.sig is unreadable/corrupt")

    # Advisories are smells, not errors — they never refuse a block that already runs
    # (validation is enforced at apply time; problems would break working third-party
    # blocks). They still get printed loudly, because an undeclared binary is exactly
    # how a block ends up passing `doctor` and dying at step 1.
    advisories = lint_block(block)

    if not problems:
        ch.success(f"{block.id} v{block.version} is valid ({len(block.steps)} steps, verify={block.verify.kind}).")
        _show_advisories(advisories)
        return
    ch.error(f"{block.id}: {len(problems)} problem(s)")
    for p_ in problems:
        ch.info(f"    - {p_}")
    _show_advisories(advisories)
    raise typer.Exit(1)


def _show_advisories(advisories: list[str]) -> None:
    if not advisories:
        return
    ch.warning(f"  {len(advisories)} advisory(ies) — not blocking, but worth fixing:")
    for a in advisories:
        ch.info(f"    ! {a}")


@block_app.command("sign")
def block_sign(block_id: str = typer.Argument(..., help="Block id (or path to a block dir)")) -> None:
    """Device-sign a block's manifest — writes BLOCK.sig (digest + Ed25519 signature).

    This makes the manifest tamper-*attributable* (not just tamper-evident): a
    verifier can prove which device signed this exact content. Third-party trust
    (a publisher key registered to a NAVIG account) is a later network feature.
    """
    import json

    from navig.blocks import find_block
    from navig.blocks.loader import parse_block_file
    from navig.license.device_keys import sign_bytes

    p = Path(block_id)
    if p.exists():
        bf = p / "BLOCK.md" if p.is_dir() else p
        block = parse_block_file(bf)
    else:
        block = find_block(block_id)
        bf = block.source_path if block else None
    if block is None or bf is None:
        ch.error(f"block '{block_id}' not found")
        raise typer.Exit(1)

    sig = sign_bytes(block.digest.encode("utf-8"))
    sidecar = {"digest": block.digest, **sig}
    (bf.parent / "BLOCK.sig").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    ch.success(f"signed {block.id} → {bf.parent / 'BLOCK.sig'}")
    ch.dim(f"  digest: {block.digest}")


@block_app.command("verify-receipt")
def block_verify_receipt(
    path: str = typer.Argument(..., help="Path to a receipt JSON (e.g. ~/.navig/runtime/receipts/<id>.json)"),
    as_json: bool = typer.Option(False, "--json", help="Emit the verdict and the chain as JSON."),
) -> None:
    """Verify a receipt's device signature — proves it wasn't altered after the run.

    Also follows the block's child-receipt chain, so a composed outcome is
    verifiable as a whole and not only at its top level.
    """
    from navig.license.device_keys import verify_receipt_dict

    p = Path(path)
    if not p.exists():
        if as_json:
            ch.raw_print(json.dumps({"error": "receipt not found", "path": str(path)}, indent=2))
        else:
            ch.error(f"receipt not found: {path}")
        raise typer.Exit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    ok, reason = verify_receipt_dict(data)
    outcome = data.get("outcome", "?")
    cap = data.get("capability", "?")

    if as_json:
        # An agent verifying an outcome needs the same facts a human gets, chain
        # included — otherwise the only machine-readable answer is "the top-level
        # signature is fine", which is exactly the half-truth this fixes.
        links = _chain_links(data, p)
        ch.raw_print(json.dumps({
            "receipt_id": data.get("receipt_id"),
            "capability": cap,
            "outcome": outcome,
            "signature": "valid" if ok is True else "unsigned" if ok is None else "invalid",
            "reason": reason,
            "chain": links,
            "chain_complete": all(x["resolved"] for x in links),
        }, indent=2))
        raise typer.Exit(1 if ok is False else 0)

    if ok is True:
        ch.success(f"receipt VALID · {cap} · {outcome} · device-signed, untampered")
    elif ok is None:
        ch.warning(f"receipt UNSIGNED · {cap} · {outcome} · {reason}")
    else:
        ch.error(f"receipt INVALID · {reason}")
        raise typer.Exit(1)

    _verify_chain(data, p)


def _verify_chain(data: dict, parent_path: Path) -> None:
    """Follow the receipt's child-block chain and verify each link.

    A block can call another block, and the parent records the children in
    ``artifacts.chain``. Listing ids nobody resolves is not evidence — so each is
    loaded and signature-checked here, and one that is MISSING is reported rather
    than passed over. Missing children do not fail the command: the parent's own
    signature is still sound, and conflating "this receipt was altered" with "a
    child's file is gone" would make the exit code useless for the first.
    """
    from navig.console_helper import Table

    links = _chain_links(data, parent_path)
    if not links:
        return

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("child receipt", no_wrap=True)
    table.add_column("capability", no_wrap=True)
    table.add_column("outcome", no_wrap=True)
    table.add_column("signature")  # the one wrappable column — it carries the reasons

    for link in links:
        if not link["resolved"]:
            table.add_row(link["id"][:8], "[dim]—[/dim]", "[red]✗ missing[/red]",
                          "[red]no receipt file for this id[/red]")
            continue
        sig = ("[green]● device-signed[/green]" if link["signature"] == "valid"
               else f"[yellow]unsigned · {link['reason']}[/yellow]"
               if link["signature"] == "unsigned"
               else f"[red]✗ INVALID · {link['reason']}[/red]")
        table.add_row(link["id"][:8], link["capability"] or "?", link["outcome"] or "?", sig)

    ch.console.print(table)
    missing = sum(1 for link in links if not link["resolved"])
    if missing:
        ch.warning(
            f"{missing} of {len(links)} child receipt(s) missing — the chain is incomplete"
        )
    else:
        ch.dim(f"  chain: {len(links)} child receipt(s), all resolved")


def _chain_links(data: dict, parent_path: Path) -> list[dict[str, Any]]:
    """Resolve and signature-check every child receipt the parent names.

    One resolver for both renderings: the human table and ``--json`` must agree,
    and the way they stop agreeing is by each doing the lookup itself.
    """
    from navig.blocks.receipts import receipts_dir
    from navig.license.device_keys import verify_receipt_dict

    links: list[dict[str, Any]] = []
    for cid in (data.get("artifacts") or {}).get("chain") or []:
        # Beside the parent first, so a copied bundle of receipts verifies as a
        # unit; then the live store.
        candidates = [parent_path.parent / f"{cid}.json", receipts_dir() / f"{cid}.json"]
        found = next((c for c in candidates if c.exists()), None)
        if found is None:
            links.append({"id": str(cid), "resolved": False, "capability": None,
                          "outcome": None, "signature": None, "reason": "no receipt file"})
            continue
        child = json.loads(found.read_text(encoding="utf-8"))
        cok, creason = verify_receipt_dict(child)
        links.append({
            "id": str(cid),
            "resolved": True,
            "capability": child.get("capability"),
            "outcome": child.get("outcome"),
            "signature": "valid" if cok is True else "unsigned" if cok is None else "invalid",
            "reason": creason,
        })
    return links


_SCAFFOLD = """\
---
id: {bid}
spec_version: 1
name: {name}
version: 0.1.0
category: general
license: MIT
description: Describe the outcome this block applies.
author: you
tags: []
target: local

inputs:
  - key: message
    type: string
    label: A value to write into the marker file
    required: true
    default: hello

steps:
  - id: write-marker
    kind: materialize
    safety: safe
    capabilities: [filesystem:write:workdir]
    dest: "{{{{workdir}}}}/.navig/blocks-out/{bid}.txt"
    content: "{{{{inputs.message}}}}\\n"

verify:
  kind: file_exists
  level: self-check
  path: "{{{{workdir}}}}/.navig/blocks-out/{bid}.txt"
---
# {name}

Explain, for a human, exactly what applying this block achieves — this body is
also the how-to article and the skill body foreign agents read.
"""


@block_app.command("new")
def block_new(
    block_id: str = typer.Argument(..., help="New block id (kebab-case)"),
    into: str = typer.Option(".navig/blocks", "--into", help="Parent dir for the new block folder."),
) -> None:
    """Scaffold a new BLOCK.md (a valid, runnable example) under .navig/blocks/<id>/."""
    from navig.blocks.policy import valid_block_id

    if not valid_block_id(block_id):
        ch.error(f"invalid block id '{block_id}' — use kebab-case (a-z, 0-9, -).")
        raise typer.Exit(1)
    dest = Path(into) / block_id
    if (dest / "BLOCK.md").exists():
        ch.warning(f"{dest / 'BLOCK.md'} already exists.")
        raise typer.Exit(1)
    dest.mkdir(parents=True, exist_ok=True)
    name = block_id.replace("-", " ").title()
    (dest / "BLOCK.md").write_text(_SCAFFOLD.format(bid=block_id, name=name), encoding="utf-8")
    ch.success(f"created {dest / 'BLOCK.md'}")
    ch.dim(f"  verify: navig block verify {dest}")
    ch.dim(f"  apply:  navig apply {block_id} --input message=hi")


# ---------------------------------------------------------------------------
# apply (registered as a flat top-level command by cli.registration)
# ---------------------------------------------------------------------------


def _coerce(value: str, itype: str) -> Any:
    if itype == "boolean":
        return str(value).lower() in ("1", "true", "yes", "on")
    if itype == "int":
        return int(value)
    if itype == "number":
        return float(value)
    return value


def _collect_inputs(block, cli_inputs: dict[str, str], inputs_file: str | None, *, interactive: bool) -> dict[str, Any]:
    """Resolve non-secret inputs. Precedence: --input > --inputs file > default > prompt.

    Secret inputs are intentionally excluded — they are resolved inside the
    runner and delivered via ``secret_env`` only.
    """
    file_values: dict[str, Any] = {}
    if inputs_file:
        file_values = json.loads(Path(inputs_file).read_text(encoding="utf-8"))

    resolved: dict[str, Any] = {}
    for inp in block.inputs:
        if inp.is_secret:
            continue
        if inp.key in cli_inputs:
            raw = cli_inputs[inp.key]
        elif inp.key in file_values:
            raw = file_values[inp.key]
        elif inp.default is not None:
            raw = inp.default
        elif interactive and inp.required:
            raw = typer.prompt(f"{inp.label or inp.key}")
        elif inp.required:
            raise ValueError(f"missing required input '{inp.key}' (pass --input {inp.key}=...)")
        else:
            continue

        val = _coerce(raw, inp.type) if isinstance(raw, str) else raw
        if inp.values and str(val) not in inp.values:
            raise ValueError(f"input '{inp.key}'='{val}' not in allowed values {inp.values}")
        if inp.pattern:
            import re as _re
            if not _re.match(inp.pattern, str(val)):
                raise ValueError(f"input '{inp.key}'='{val}' does not match pattern {inp.pattern}")
        if inp.type == "path" and inp.must_exist and not Path(str(val)).exists():
            raise ValueError(f"input '{inp.key}': path '{val}' does not exist")
        resolved[inp.key] = val
    return resolved


def apply_command(
    spec: str = typer.Argument(..., help="Block id, or install spec (block:owner/repo/... / github:...)."),
    input_: list[str] = typer.Option(None, "--input", "-i", help="k=v (repeatable)."),
    inputs_file: str = typer.Option(None, "--inputs", help="Path to a JSON file of inputs."),
    approve: list[str] = typer.Option(None, "--approve", help="Approve a named destructive step (repeatable)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip moderate confirmations (NOT enough for destructive steps)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the resolved plan; resolve no secrets, no network, no writes."),
    json_out: bool = typer.Option(False, "--json", help="Emit the receipt JSON to stdout."),
    workdir: str = typer.Option(None, "--workdir", help="Working directory (default: current space root)."),
    dev_untrusted: bool = typer.Option(False, "--dev-untrusted", help="Allow installing a local/unverified block."),
) -> None:
    """Apply a block — run its outcome end-to-end and write a receipt."""
    from navig.blocks import build_receipt, find_block, persist_receipt, validate_block
    from navig.blocks.runner import apply_block
    from navig.operation_recorder import OperationType, RecordedOperation
    from navig.platform.paths import find_app_root

    cli_inputs = dict(kv.split("=", 1) for kv in (input_ or []) if "=" in kv)
    approvals = set(approve or [])

    # ── Resolve: installed by id, else install from spec ──
    block_id = spec.split(":")[-1].strip("/").split("/")[-1]
    block = find_block(block_id)
    if block is None and (":" in spec or "/" in spec):
        _install_block(spec, dev_untrusted=dev_untrusted)
        block = find_block(block_id)
    if block is None:
        ch.warning(f"block '{spec}' not found. Install it: navig install add block:navig-run/community/blocks/{block_id}")
        raise typer.Exit(1)

    problems = validate_block(block)
    if problems:
        ch.error(f"block '{block.id}' is invalid — refusing to run:")
        for p_ in problems:
            ch.info(f"    - {p_}")
        raise typer.Exit(1)

    try:
        inputs = _collect_inputs(block, cli_inputs, inputs_file, interactive=not (yes or dry_run))
    except (ValueError, json.JSONDecodeError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    wd = Path(workdir) if workdir else (find_app_root() or Path.cwd())

    with RecordedOperation(
        command=f"navig apply {block.id}",
        op_type=OperationType.WORKFLOW_RUN,
        tags=["block", block.id],
    ) as rec:
        run = apply_block(block, inputs, yes=yes, dry_run=dry_run, approvals=approvals, workdir=wd)
        rec.success = run.outcome in ("succeeded", "planned")
        rec.output = f"{block.id}:{run.outcome}"

    if dry_run:
        return

    receipt = build_receipt(block, run, inputs, trust="first-party")
    path = persist_receipt(receipt)

    if json_out:
        print(receipt.to_json())
    else:
        icon = "✓" if run.outcome == "succeeded" else "✗"
        ch.info(f"{icon} {block.id} → {run.outcome} · verification: {run.verification_level}")
        for s in run.steps:
            mark = {"ok": "✓", "manual_ack": "◻", "failed": "✗", "blocked": "⛔"}.get(s.status, "·")
            ch.dim(f"    {mark} {s.id} \\[{s.kind}] {s.status}")
        if run.error:
            ch.warning(f"    {run.error}")
        ch.dim(f"  receipt: {path}")
    if run.outcome not in ("succeeded", "planned"):
        raise typer.Exit(2)


def _install_block(spec: str, *, dev_untrusted: bool) -> None:
    from navig.blocks.policy import TrustError, check_install_trust
    from navig.commands.install import _parse_spec, install_asset

    info = _parse_spec(spec, default_type="block")
    try:
        check_install_trust(info.get("owner"), info.get("repo"), dev_untrusted=dev_untrusted)
    except TrustError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    try:
        install_asset(spec, default_type="block")
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc
