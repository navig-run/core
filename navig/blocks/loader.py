"""Block discovery, parsing, validation, and skill-shim projection.

A **Block** is an installable, machine-executable *outcome* — "apply
`steam-build-upload`". It is a superset of the Claude/NAVIG ``SKILL.md`` format:
a ``BLOCK.md`` whose YAML frontmatter is the **machine spec** (typed inputs,
ordered steps, verification, outputs, provenance) and whose markdown body is the
**human how-to article** (also the skill body foreign agents read).

This module owns everything *static* about a block: the dataclasses, the parser,
directory discovery, the linter (:func:`validate_block`), the content digest, the
generated ``SKILL.md`` shim (so an installed block appears to Claude Code as a
skill), and legacy playbook/workflow normalization. Execution lives in
``runner.py``; trust/risk/lockfile in ``policy.py``.

Design notes:
  * Frontmatter is authoritative for execution; the body is authoritative for
    understanding.
  * ``command`` steps take an ``argv`` list — never a shell string. Secrets never
    appear in argv (see :func:`validate_block`); they flow via ``secret_env``.
  * ``spec_version`` is pinned to 1; unknown future versions are rejected loudly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

SPEC_VERSION = 1

# Step kinds understood by the runner.
_STEP_KINDS = {"materialize", "command", "skill", "prompt", "instruction", "block"}
# Verify kinds understood by the runner. ``command`` and ``file_exists`` are
# machine self-checks; ``none`` records that no machine verification exists.
_VERIFY_KINDS = {"none", "file_exists", "command"}
_INPUT_TYPES = {"string", "int", "number", "boolean", "path", "enum", "secret"}

# Template token pattern: {{inputs.x}}, {{outputs.x}}, {{workdir}}, {{config_dir}}, {{vault.x}}
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class BlockError(ValueError):
    """Raised when a BLOCK.md is structurally invalid (parse-time)."""


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------


@dataclass
class BlockInput:
    key: str
    type: str = "string"
    label: str = ""
    required: bool = False
    default: Any = None
    pattern: str | None = None
    values: list[str] = field(default_factory=list)  # enum
    must_exist: bool = False  # path
    vault: str | None = None  # secret source hint

    @property
    def is_secret(self) -> bool:
        return self.type == "secret"


@dataclass
class BlockStep:
    id: str
    kind: str
    safety: str = "safe"  # descriptive only — runner computes effective risk
    capabilities: list[str] = field(default_factory=list)
    # command
    argv: list[str] = field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int = 600
    network: str = "none"  # none | required
    secret_env: dict[str, str] = field(default_factory=dict)  # {ENV_VAR: inputs.key}
    capture: dict[str, str] = field(default_factory=dict)  # {out_key: regex}
    # materialize
    src: str | None = None
    content: str | None = None
    dest: str | None = None
    # skill
    skill: str | None = None
    # prompt / instruction
    text: str = ""
    # block (composition)
    use: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    # per-step verify
    verify: dict[str, Any] | None = None


@dataclass
class BlockVerify:
    kind: str = "none"
    level: str = "self-check"  # self-check | external-check when passing
    argv: list[str] = field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int = 120
    path: str | None = None  # file_exists
    expect: dict[str, Any] = field(default_factory=dict)  # exit_code / output_contains / output_regex


@dataclass
class Block:
    id: str
    name: str
    version: str
    category: str
    license: str
    spec_version: int
    inputs: list[BlockInput]
    requires: dict[str, Any]
    steps: list[BlockStep]
    verify: BlockVerify
    outputs: list[dict[str, Any]]
    receipt: dict[str, Any]
    marketplace: dict[str, Any] | None
    body_markdown: str
    source_path: Path
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    target: str = "local"
    digest: str = ""  # sha256 of canonical frontmatter+body, filled by compute_digest

    def secret_keys(self) -> set[str]:
        return {i.key for i in self.inputs if i.is_secret}


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body). Empty dict if no ``---`` block."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml  # lazy — keep cold CLI fast
    except ImportError:  # pragma: no cover
        return {}, parts[2].lstrip()
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception as exc:  # noqa: BLE001
        raise BlockError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise BlockError("frontmatter must be a mapping")
    return fm, parts[2].lstrip()


def _as_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    return []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def compute_digest(fm: dict[str, Any], body: str) -> str:
    """Deterministic sha256 over canonicalized frontmatter + body.

    Canonical = JSON with sorted keys, so cosmetic YAML reordering does not
    change identity. This is the value pinned in ``blocks.lock.json``.
    """
    import json

    canonical = json.dumps(fm, sort_keys=True, separators=(",", ":"), default=str)
    h = hashlib.sha256()
    h.update(canonical.encode("utf-8"))
    h.update(b"\x00")
    h.update(body.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _parse_input(raw: dict[str, Any]) -> BlockInput:
    key = str(raw.get("key") or "").strip()
    if not key:
        raise BlockError("input missing 'key'")
    itype = str(raw.get("type", "string"))
    if itype not in _INPUT_TYPES:
        raise BlockError(f"input '{key}': unknown type '{itype}'")
    return BlockInput(
        key=key,
        type=itype,
        label=str(raw.get("label", "")),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        pattern=raw.get("pattern"),
        values=_as_list(raw.get("values")),
        must_exist=bool(raw.get("must_exist", False)),
        vault=raw.get("vault"),
    )


def _parse_step(raw: dict[str, Any], idx: int) -> BlockStep:
    sid = str(raw.get("id") or f"step-{idx + 1}")
    kind = str(raw.get("kind") or "").strip()
    if kind not in _STEP_KINDS:
        raise BlockError(f"step '{sid}': unknown kind '{kind}' (allowed: {sorted(_STEP_KINDS)})")
    return BlockStep(
        id=sid,
        kind=kind,
        safety=str(raw.get("safety", "safe")),
        capabilities=_as_list(raw.get("capabilities")),
        argv=[str(a) for a in raw.get("argv", [])] if isinstance(raw.get("argv"), list) else [],
        cwd=raw.get("cwd"),
        timeout_seconds=int(raw.get("timeout_seconds", 600)),
        network=str(raw.get("network", "none")),
        secret_env={str(k): str(v) for k, v in (raw.get("secret_env") or {}).items()},
        capture={str(k): str(v) for k, v in (raw.get("capture") or {}).items()},
        src=raw.get("src"),
        content=raw.get("content"),
        dest=raw.get("dest"),
        skill=raw.get("skill"),
        text=str(raw.get("text", "")),
        use=raw.get("use"),
        inputs=dict(raw.get("inputs") or {}),
        verify=raw.get("verify"),
    )


def _parse_verify(raw: Any) -> BlockVerify:
    if not raw:
        return BlockVerify(kind="none")
    if not isinstance(raw, dict):
        raise BlockError("verify must be a mapping")
    kind = str(raw.get("kind", "none"))
    if kind not in _VERIFY_KINDS:
        raise BlockError(f"verify: unknown kind '{kind}' (allowed: {sorted(_VERIFY_KINDS)})")
    return BlockVerify(
        kind=kind,
        level=str(raw.get("level", "self-check")),
        argv=[str(a) for a in raw.get("argv", [])] if isinstance(raw.get("argv"), list) else [],
        cwd=raw.get("cwd"),
        timeout_seconds=int(raw.get("timeout_seconds", 120)),
        path=raw.get("path"),
        expect=dict(raw.get("expect") or {}),
    )


def parse_block_file(path: Path) -> Block | None:
    """Parse a ``BLOCK.md`` into a :class:`Block`. Returns None on read error.

    Raises :class:`BlockError` on structural problems so ``navig block verify``
    can surface a precise message; discovery callers swallow that per-file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("blocks.loader: cannot read {}: {}", path, exc)
        return None

    fm, body = _split_frontmatter(text)
    if not fm:
        raise BlockError(f"{path} has no YAML frontmatter (a BLOCK.md requires a machine spec)")

    spec_v = int(fm.get("spec_version", 1))
    if spec_v != SPEC_VERSION:
        raise BlockError(
            f"{path}: spec_version {spec_v} unsupported (this build understands {SPEC_VERSION})"
        )

    folder_slug = _slug(path.parent.name)
    bid = str(fm.get("id") or folder_slug)

    inputs = [_parse_input(i) for i in (fm.get("inputs") or []) if isinstance(i, dict)]
    steps = [_parse_step(s, i) for i, s in enumerate(fm.get("steps") or []) if isinstance(s, dict)]

    block = Block(
        id=bid,
        name=str(fm.get("name") or path.parent.name),
        version=str(fm.get("version", "0.1.0")),
        category=str(fm.get("category", "general")),
        license=str(fm.get("license", "unspecified")),
        spec_version=spec_v,
        inputs=inputs,
        requires=dict(fm.get("requires") or {}),
        steps=steps,
        verify=_parse_verify(fm.get("verify")),
        outputs=list(fm.get("outputs") or []),
        receipt=dict(fm.get("receipt") or {}),
        marketplace=fm.get("marketplace") if isinstance(fm.get("marketplace"), dict) else None,
        body_markdown=body,
        source_path=path,
        description=str(fm.get("description", "")),
        author=str(fm.get("author", "")),
        tags=_as_list(fm.get("tags")),
        allowed_tools=_as_list(fm.get("allowed-tools")) or _as_list(fm.get("allowed_tools")),
        target=str(fm.get("target", "local")),
    )
    block.digest = compute_digest(fm, body)
    return block


# ---------------------------------------------------------------------------
# Validation (linter)
# ---------------------------------------------------------------------------


def _declared_tokens(block: Block) -> set[str]:
    toks = {"workdir", "config_dir"}
    toks |= {f"inputs.{i.key}" for i in block.inputs}
    toks |= {f"outputs.{o.get('key')}" for o in block.outputs if o.get("key")}
    return toks


def validate_block(block: Block) -> list[str]:
    """Return a list of human-readable problems ([] == valid).

    Enforces the Stage-1 security floor at author time:
      * required identity fields present;
      * ``command`` steps use argv (no shell), and no secret input is ever
        interpolated into argv (secrets flow through ``secret_env`` only);
      * ``exec`` steps declare a capability;
      * every ``{{inputs.*}}`` / ``{{outputs.*}}`` token is declared;
      * ``target: local`` (remote blocks are a later stage).
    """
    problems: list[str] = []

    for field_name in ("id", "name", "version", "category", "license"):
        if not getattr(block, field_name, None):
            problems.append(f"missing required field '{field_name}'")

    if block.target != "local":
        problems.append(f"target '{block.target}' unsupported in this stage (only 'local')")

    declared = _declared_tokens(block)
    secret_keys = block.secret_keys()

    def _check_tokens(where: str, s: str) -> None:
        for tok in _TOKEN_RE.findall(s or ""):
            root = tok.split(".", 1)[0]
            if root == "vault":
                problems.append(
                    f"{where}: secrets ({{{{{tok}}}}}) must not be interpolated here — "
                    f"declare a secret input and deliver it via secret_env"
                )
                continue
            if tok not in declared and root not in ("workdir", "config_dir"):
                problems.append(f"{where}: undeclared template token '{{{{{tok}}}}}'")

    seen_ids: set[str] = set()
    for step in block.steps:
        if step.id in seen_ids:
            problems.append(f"duplicate step id '{step.id}'")
        seen_ids.add(step.id)

        if step.kind == "command":
            if not step.argv:
                problems.append(f"step '{step.id}': command requires a non-empty 'argv'")
            for element in step.argv:
                _check_tokens(f"step '{step.id}' argv", element)
                for sk in secret_keys:
                    if f"inputs.{sk}" in element:
                        problems.append(
                            f"step '{step.id}': secret input '{sk}' must not appear in argv "
                            f"(use secret_env)"
                        )
            if step.network not in ("none", "required"):
                problems.append(f"step '{step.id}': network must be 'none' or 'required'")
            # secret_env must reference declared secret inputs
            for env_var, ref in step.secret_env.items():
                key = ref.split(".", 1)[-1] if ref.startswith("inputs.") else ref
                if key not in {i.key for i in block.inputs}:
                    problems.append(f"step '{step.id}': secret_env {env_var} -> undeclared input '{key}'")
            if not step.capabilities:
                problems.append(f"step '{step.id}': exec/command steps must declare capabilities")

        elif step.kind == "materialize":
            if not step.dest:
                problems.append(f"step '{step.id}': materialize requires 'dest'")
            if not step.src and step.content is None:
                problems.append(f"step '{step.id}': materialize requires 'src' or 'content'")
            _check_tokens(f"step '{step.id}' dest", step.dest or "")
            if step.content:
                _check_tokens(f"step '{step.id}' content", step.content)
                for sk in secret_keys:
                    if f"inputs.{sk}" in step.content:
                        problems.append(
                            f"step '{step.id}': secret input '{sk}' must not appear in materialized content"
                        )

        elif step.kind == "block":
            if not step.use:
                problems.append(f"step '{step.id}': block step requires 'use' (target block id)")

        elif step.kind == "skill":
            if not step.skill:
                problems.append(f"step '{step.id}': skill step requires 'skill' (skill id)")

        elif step.kind in ("prompt", "instruction"):
            if not step.text:
                problems.append(f"step '{step.id}': {step.kind} requires 'text'")
            _check_tokens(f"step '{step.id}' text", step.text)

    # top-level verify tokens
    if block.verify.kind == "command":
        for element in block.verify.argv:
            _check_tokens("verify argv", element)
    elif block.verify.kind == "file_exists":
        _check_tokens("verify path", block.verify.path or "")
        if not block.verify.path:
            problems.append("verify(file_exists): requires 'path'")

    problems.extend(_validate_requires(block.requires))

    return problems


def lint_block(block: Block) -> list[str]:
    """Advisory findings — real smells that are **not** hard errors ([] == clean).

    Kept separate from :func:`validate_block` on purpose: validation is *enforced* at
    apply time, so promoting these to problems would refuse to run third-party blocks
    that already work. These are surfaced by ``navig block verify`` as warnings.

    Today's one rule — **an undeclared binary**: a step that runs ``msstore`` while
    ``requires.tools`` is silent means ``navig block doctor`` cheerfully reports "no
    requirements — ready to apply", and then the apply dies at step 1 with "command not
    found". Declaring the tool is what turns that into a fail-fast with an install hint.
    """
    from navig.blocks.policy import requirement_entries

    declared = {name for name, _ in requirement_entries((block.requires or {}).get("tools"))}
    findings: list[str] = []
    seen: set[str] = set()

    def _check(where: str, argv: list[str]) -> None:
        if not argv:
            return
        exe = argv[0]
        # A templated argv[0] is resolved at apply time — nothing to check statically.
        # `navig` is the host CLI running the block; it is always present.
        if not exe or "{{" in exe or exe == "navig" or exe in declared or exe in seen:
            return
        seen.add(exe)
        findings.append(
            f"{where} runs '{exe}' but requires.tools does not declare it — "
            f"`navig block doctor {block.id}` would report 'ready' and the apply would "
            f"fail at that step. Declare it: requires.tools: [{{name: {exe}, install: …}}]"
        )

    for step in block.steps:
        if step.kind == "command":
            _check(f"step '{step.id}'", list(step.argv or []))
    if block.verify.kind == "command":
        _check("verify", list(block.verify.argv or []))
    return findings


# `requires:` keys the runner actually enforces. Anything else is a typo or a
# precondition the gate would silently ignore — and a *declared but ignored*
# requirement is worse than none: the author believes it is enforced.
_REQUIRES_KEYS = ("tools", "plugins", "detect")


def _validate_requires(requires) -> list[str]:
    """Lint the `requires:` block at author time, so `navig block verify` catches a
    malformed precondition instead of `navig apply` failing on a live machine."""
    problems: list[str] = []
    if requires is None:
        return problems
    if not isinstance(requires, dict):
        return ["requires: must be a mapping (tools / plugins / detect)"]

    for key in requires:
        if key == "permissions":
            problems.append(
                "requires.permissions: not enforced — the runner would silently ignore it. "
                "Express the precondition as a `detect` probe (a read-only argv check) instead."
            )
        elif key not in _REQUIRES_KEYS:
            problems.append(
                f"requires.{key}: unknown key (expected one of {', '.join(_REQUIRES_KEYS)})")

    for key in ("tools", "plugins"):
        items = requires.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            problems.append(f"requires.{key}: must be a list")
            continue
        for i, item in enumerate(items):
            where = f"requires.{key}[{i}]"
            if isinstance(item, str):
                if not item.strip():
                    problems.append(f"{where}: empty name")
            elif isinstance(item, dict):
                if not str(item.get("name") or "").strip():
                    problems.append(f"{where}: mapping form requires a 'name'")
                for extra in item:
                    if extra not in ("name", "install"):
                        problems.append(f"{where}: unknown key '{extra}' (expected name/install)")
            else:
                problems.append(f"{where}: must be a name, or a mapping with name/install")

    detect = requires.get("detect")
    if detect is not None:
        if not isinstance(detect, list):
            problems.append("requires.detect: must be a list of probes")
        else:
            for i, probe in enumerate(detect):
                where = f"requires.detect[{i}]"
                if not isinstance(probe, dict):
                    problems.append(f"{where}: must be a mapping")
                    continue
                run = probe.get("run")
                if not isinstance(run, list) or not run:
                    problems.append(f"{where}: requires a non-empty 'run' argv list")
                elif not all(isinstance(a, str) for a in run):
                    problems.append(f"{where}: 'run' must be argv strings (no shell string)")
                if "expect_exit" in probe and not isinstance(probe["expect_exit"], int):
                    problems.append(f"{where}: 'expect_exit' must be an integer")
    return problems


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def get_block_dirs() -> list[Path]:
    """Return block roots to scan: builtin store, user store, project-local, and
    any `blocks/` dir shipped by an installed plugin (so a plugin can own its
    blocks — e.g. navig-mobile's `app-ui-verify`)."""
    candidates: list[Path] = []
    try:
        from navig.platform.paths import builtin_store_dir, find_app_root, store_dir

        for fn in (builtin_store_dir, store_dir):
            try:
                d = fn() / "blocks"
                if d.exists():
                    candidates.append(d)
            except Exception:  # noqa: BLE001
                pass
        root = find_app_root()
        if root is not None:
            local = root / ".navig" / "blocks"
            if local.exists():
                candidates.append(local)
    except ImportError:  # pragma: no cover
        pass

    # Installed plugins that ship a `blocks/` dir (both install shapes) — mirrors
    # how skills are discovered from plugins (see skills.loader.get_skill_dirs), so
    # a plugin can OWN its own blocks (e.g. navig-mobile's `app-ui-verify`). A
    # plugin block becomes applyable exactly when its plugin is installed — which
    # is correct, since the block can't run without the plugin's commands.
    try:
        from navig.plugins.package import plugin_capability_dirs

        candidates.extend(plugin_capability_dirs("blocks"))
    except Exception:  # noqa: BLE001 — plugin host optional; never block discovery
        pass

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def discover_blocks(dirs: list[Path] | None = None) -> list[Block]:
    """Scan for ``BLOCK.md`` files and parse them (malformed ones are skipped)."""
    if dirs is None:
        dirs = get_block_dirs()
    out: list[Block] = []
    seen_files: set[Path] = set()
    for d in dirs:
        for bf in sorted(d.rglob("BLOCK.md")):
            rp = bf.resolve()
            if rp in seen_files:
                continue
            seen_files.add(rp)
            try:
                block = parse_block_file(bf)
            except BlockError as exc:
                logger.warning("blocks.loader: skipping {} — {}", bf, exc)
                continue
            if block is not None:
                out.append(block)
    return out


def blocks_by_id(blocks: list[Block] | None = None) -> dict[str, Block]:
    if blocks is None:
        blocks = discover_blocks()
    return {b.id: b for b in blocks}


def find_block(block_id: str) -> Block | None:
    return blocks_by_id().get(block_id)


# ---------------------------------------------------------------------------
# Skill-shim projection (so installed blocks appear to Claude Code as skills)
# ---------------------------------------------------------------------------


def write_skill_shim(block_dir: Path) -> Path | None:
    """Generate a deterministic ``SKILL.md`` beside a block's ``BLOCK.md``.

    The shim lets any SKILL.md consumer (Claude Code via ``.claude/skills``,
    foreign agents) discover the block: the body is the how-to article plus a
    machine-spec footer that says ``navig apply <id>``. It is regenerated on
    every install — never hand-edited (``generated-by: navig-block``).
    """
    bf = block_dir / "BLOCK.md"
    if not bf.exists():
        return None
    try:
        block = parse_block_file(bf)
    except BlockError:
        return None
    if block is None:
        return None

    tools = ", ".join(block.allowed_tools) if block.allowed_tools else ""
    inputs_lines = "\n".join(
        f"- `{i.key}` ({i.type}{', required' if i.required else ''})"
        + (f" — {i.label}" if i.label else "")
        for i in block.inputs
    ) or "- (none)"
    example = f"navig apply {block.id}" + "".join(
        f" --input {i.key}=<{i.type}>" for i in block.inputs if i.required
    )

    fm_lines = [
        "---",
        f"name: {block.name}",
        f"description: {block.description or block.name}",
    ]
    if tools:
        fm_lines.append(f"allowed-tools: {tools}")
    fm_lines += [
        f"version: {block.version}",
        "generated-by: navig-block",
        f"block-id: {block.id}",
        "---",
    ]
    footer = (
        "\n\n## Apply this outcome\n\n"
        f"This is a NAVIG **block**. Run it end-to-end with:\n\n```\n{example}\n```\n\n"
        "### Inputs\n\n"
        f"{inputs_lines}\n"
    )
    shim = "\n".join(fm_lines) + "\n\n" + block.body_markdown.rstrip() + footer + "\n"
    out = block_dir / "SKILL.md"
    out.write_text(shim, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Legacy normalization (playbook/workflow install types → minimal block)
# ---------------------------------------------------------------------------


def normalize_legacy_asset(dest: Path, asset_id: str) -> Path | None:
    """Give an aliased playbook/workflow a machine face.

    When a ``playbook:``/``workflow:`` spec installs a folder that has no
    ``BLOCK.md``, synthesize a minimal one: identity from any existing
    frontmatter, a single ``instruction`` step carrying the body, and no
    machine verification (receipt records ``verify: none``). Authors upgrade by
    adding real steps + verify.
    """
    if (dest / "BLOCK.md").exists():
        return dest / "BLOCK.md"

    src = None
    for cand in ("SKILL.md", "README.md", "playbook.md", "workflow.md", f"{asset_id}.md"):
        if (dest / cand).exists():
            src = dest / cand
            break
    body = ""
    name = asset_id.replace("-", " ").title()
    if src is not None:
        raw = src.read_text(encoding="utf-8", errors="replace")
        fm, body = _split_frontmatter(raw)
        name = str(fm.get("name") or name)

    safe_body = body.replace("---", "———")  # avoid nested frontmatter fences
    manifest = (
        "---\n"
        f"id: {asset_id}\n"
        "spec_version: 1\n"
        f"name: {name}\n"
        "version: 0.1.0\n"
        "category: legacy\n"
        "license: unspecified\n"
        "description: Imported legacy playbook/workflow (normalized to a block).\n"
        "target: local\n"
        "inputs: []\n"
        "steps:\n"
        "  - id: run\n"
        "    kind: instruction\n"
        "    safety: safe\n"
        "    text: |\n"
        + "".join(f"      {line}\n" for line in (safe_body or name).splitlines())
        + "verify:\n  kind: none\n"
        "---\n\n"
        f"# {name}\n\n{body}\n"
    )
    out = dest / "BLOCK.md"
    out.write_text(manifest, encoding="utf-8")
    return out
