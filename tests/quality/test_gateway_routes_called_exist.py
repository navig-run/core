"""No in-process caller may request a gateway route+method this gateway does not serve.

#1050 is why this exists. Four Telegram slash commands were wired up, proved to
DISPATCH, and shipped — and all four still failed, because ``/mesh/status``,
``/mesh/config``, ``/mesh/handoff`` and ``/mesh/election/state`` are not routes the
gateway serves at all. **Four of the five endpoints those handlers called were 404s.**

Reachability had been checked; existence had not. Code that has never run has never
been checked against its own dependencies — which is exactly why it stayed dead: nothing
exercised it, so nobody noticed the API had drifted out from under it.

Nothing else catches this. The caller and the route live in different packages, the path
is a string on both sides, and a 404 arrives at runtime as an exception inside a
``try:`` whose handler reports "mesh unavailable" — indistinguishable from a mesh that
is genuinely down. ruff sees two valid string literals; ``check_module_attrs`` sees no
attribute; the type checker sees ``str``.

Both sides are resolved from the source:

* **served** — every literal path registered with ``app.router.add_<verb>(...)`` or an
  ``aiohttp.web.<verb>(...)`` route definition, **with the method it accepts**. Dynamic
  segments (``{name}``) become single-segment wildcards. This side is deliberately
  PERMISSIVE: a route it cannot resolve statically is treated as "might exist", so the
  guard only ever fires on a path that is *definitely* unserved. A false negative costs
  nothing new; a false positive gets a guard deleted.
* **requested** — calls to a local-gateway request helper with a statically-known path,
  plus a direct ``session.get(f"{gateway_base_url()}/path")``, **each with the method the
  caller uses** (an explicit ``method`` argument; otherwise the helper name).

**The method is half the contract, and getting it wrong fails identically.** 335 of the
gateway's 376 routes accept exactly ONE method, so a caller using the wrong verb gets a
405 — which, like the 404, arrives as an exception inside a ``try:`` and is reported as
"the subsystem is unavailable". Checking the path alone would validate half of a
two-part contract while reading as though it validated the whole thing. Zero mismatches
exist today; this is a floor placed before something falls through it, exactly like the
``registry/`` suites that were green when they were first wired into the gate.

Verb resolution is **permissive when unsure, on both sides**: a call whose method is not
a static literal, and a route whose method cannot be read, are simply not verb-checked
(the path check still applies). Two ratio floors stop that leniency from quietly becoming
total — see ``_MIN_VERB_RESOLUTION``.

⚠ Helpers are discovered **per module, by shape** — a function taking a ``path``
parameter whose body builds its URL from a gateway-base symbol. Resolving them by NAME
across the tree is wrong and was measured to be wrong: ``navig/cloud/broker_client.py``
and ``navig/connectors/perplexity/`` each define their own ``_get``/``_post`` aimed at a
REMOTE service, and a global name map reported all 7 of those calls as unserved gateway
routes. Same name-collision trap as #1020.

⚠ Scope is ``core/navig`` because that is where the surface is, measured rather than
assumed: no plugin and not ``private/harbor`` defines a gateway request helper. If one
ever does, it is caught by the same shape — widen ``_ROOTS``.

**What this guard deliberately does NOT catch.** A path that a *parameterised* route
would accept is treated as served, because aiohttp really would route it: ``/tasks/statz``
resolves against ``/tasks/{task_id}``. So a typo in a final segment that collides with a
dynamic route reaches the wrong handler rather than a 404, and that is a different bug
class from the one here. Mutating a final segment under a parameterised parent is
therefore a bad way to test this guard — mutate the first segment.

**The DOCS are a consumer of this contract too, and that is how one got through.**
`/mesh/topology` was documented — a `curl` command *and* an API-table row in
``docs/guides/mesh-multi-machine.md`` — and never registered, so the guide's own command
returned a 404 while the code check above stayed green (no caller in Python ever named
it). ``test_documented_gateway_urls_name_routes_that_exist`` closes that: every
`http://localhost:<gateway port>/…` in either doc tree must name a served route. The
port is IMPORTED from ``_daemon_defaults``, not typed, and it is the scoping signal — a
doc URL on another port belongs to another service, and three of them do.

Teeth, verified by mutation (2026-08-20) — **seven mutations, all caught**: the path
check across all four caller forms it recognises (bound-method helper — the #1050 shape,
direct f-string, plain-function helper, method helper in another module), and the verb
check across its three (explicit ``method`` argument, helper name, direct f-string). All
four anti-vacuity floors were verified the same way, by collapsing each input in turn.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
REPO = CORE.parent
_ROOTS = [CORE / "navig"]

# Markdown that tells a user to curl the gateway. Both trees, because the HANDBOOK lives
# under `core/docs` and the guides under the repo-root `docs`.
_DOC_ROOTS = [REPO / "docs", CORE / "docs"]

# Registration idiom -> the HTTP method it serves. `add_route` carries its method as the
# first argument instead.
_ADD_VERBS = {
    "add_get": "GET", "add_post": "POST", "add_put": "PUT", "add_delete": "DELETE",
    "add_patch": "PATCH", "add_head": "HEAD", "add_route": None,
}
_WEB_VERBS = {"get", "post", "put", "delete", "patch", "head"}

# A function whose body mentions one of these builds a URL against the LOCAL gateway.
# Every spelling that exists in the tree, not just the ones currently load-bearing:
# `_gw_base_url` (commands/gateway.py) is reached today only because that module's
# helpers also chain through `gateway_request`, so a future helper using it ALONE would
# be invisible. Adding it was measured to change nothing now — 33 call sites either way
# — which is the point: it closes a hole rather than fixing a symptom.
_GATEWAY_BASE_SYMBOLS = {
    "gateway_base_url", "_gateway_base", "_gw", "_gw_base", "_gw_base_url",
    "gateway_request", "_gw_request",
}

# A requested path that is knowingly served by something other than this gateway's own
# router. Empty today, and that is the point: every in-process caller currently resolves.
_ALLOWED_UNSERVED: dict[str, str] = {}

# Measured floors. A scan that silently reads nothing looks exactly like a clean run,
# and this guard's whole value is the comparison of two sets — either one collapsing to
# empty makes it pass vacuously. Bounds are set below the measurement (376 routes / 33
# call sites on 2026-08-20) with room for ordinary churn.
_MIN_ROUTES = 300
_MIN_CALL_SITES = 18
# The verb check has its OWN way of going vacuous that the two above cannot see: if verb
# resolution quietly started returning None, every call site would still be counted and
# every path still checked, while the method half silently stopped being enforced.
# Measured 33/33 call sites and 376/376 routes resolved; the floor is a ratio so it does
# not have to move with ordinary churn.
_MIN_VERB_RESOLUTION = 0.80

_WILDCARD = "\x00"  # stands in for an interpolated {expr} in an f-string


# Cheap text gates applied before parsing. This guard runs on every push, so the cost of
# `ast.parse` over the whole package is the thing to avoid: only a file that could
# possibly REGISTER a route or CALL one is worth parsing. Measured: 16.0s -> 3.6s, with
# an identical result set (asserted by the floors below).
_MAY_SERVE = ("add_get", "add_post", "add_put", "add_delete", "add_patch", "add_head",
              "add_route", "web.get", "web.post", "web.put", "web.delete", "web.patch",
              "web.head")
_MAY_CALL = tuple(_GATEWAY_BASE_SYMBOLS)


def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def _candidate_files() -> list[tuple[Path, str, ast.Module]]:
    """(path, text, tree) for files that could serve or call a route. Parsed once."""
    out: list[tuple[Path, str, ast.Module]] = []
    for root in _ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*.py"):
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if not (any(t in text for t in _MAY_SERVE) or any(t in text for t in _MAY_CALL)):
                continue
            tree = _parse(text)
            if tree is not None:
                out.append((f, text, tree))
    return out


def _static_path(node: ast.AST) -> str | None:
    """A literal or f-string as a path pattern; each ``{expr}`` becomes a wildcard."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else _WILDCARD
            for v in node.values
        )
    return None


def _to_regex(pattern: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]*\}|" + _WILDCARD + ")", pattern)
    body = "".join(
        "[^/]+" if p.startswith("{") or p == _WILDCARD else re.escape(p)
        for p in parts
    )
    return re.compile(f"^{body}$")


def _served_routes(files: list[tuple[Path, str, ast.Module]]) -> dict[str, set[str]]:
    """``{path pattern: {method, …}}`` this gateway registers, as the source can say."""
    routes: dict[str, set[str]] = {}
    for _f, _text, tree in files:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            verb: str | None
            if attr in _ADD_VERBS:
                # add_route(method, path, handler); add_get(path, handler)
                idx = 1 if attr == "add_route" else 0
                verb = _ADD_VERBS[attr]
                if verb is None:  # add_route — the method is its first argument
                    m = node.args[0] if node.args else None
                    # A non-STRING literal must resolve to "unknown", not to a stringified
                    # verb: `str(3).upper()` is `"3"`, which matches no caller and would
                    # make the route look method-restricted when we simply cannot tell.
                    verb = (
                        m.value.upper()
                        if isinstance(m, ast.Constant) and isinstance(m.value, str)
                        else None
                    )
            elif attr in _WEB_VERBS and isinstance(node.func.value, ast.Name) and node.func.value.id == "web":
                # aiohttp route defs handed to add_routes(...) — e.g. the webhook receiver
                idx = 0
                verb = attr.upper()
            else:
                continue
            if len(node.args) <= idx:
                continue
            arg = node.args[idx]
            # ⚠ LITERALS ONLY on this side. An f-string registration collapses to a
            # wildcard, and a wildcard route does not make the guard permissive — it
            # makes it BLIND. `deck/__init__.py` mounts static files with
            # `add_get(f"/{f.name}")`, which as a pattern matches ANY single-segment
            # path: with it in the served set, a caller asking for `/statuz` resolved
            # against it and the guard passed. Measured — that mutation was the second
            # one to report NO TEETH.
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            if arg.value.startswith("/"):
                # An unresolvable method (a non-literal `add_route(m, …)`) records the
                # path with NO verb, which the verb check reads as "any" — same
                # permissive-when-unsure rule the path side follows.
                routes.setdefault(arg.value, set())
                if verb:
                    routes[arg.value].add(verb)
    return routes


def _helper_verb(name: str) -> str | None:
    """The fixed HTTP method a helper's NAME implies, when it takes no ``method`` param.

    Only used as a fallback — a helper that accepts ``method`` always wins, because the
    caller chooses there. Substring matching is safe here precisely because it is a
    fallback: the worst case is `None` (skip the verb check), never a wrong verb.
    """
    low = name.lower()
    for verb in ("delete", "patch", "post", "put", "get", "head"):
        if verb in low:
            return verb.upper()
    return None


def _gateway_helpers(tree: ast.Module) -> dict[str, tuple[int, int | None, str | None]]:
    """{helper: (path index, method index or None, fixed verb or None)} for THIS module.

    ⚠ A leading ``self``/``cls`` is dropped, and getting that wrong is not theoretical —
    it is how the first version of this guard reported NO TEETH. ``_mesh_get(self, path)``
    puts ``path`` at signature index 1, but the bound call ``self._mesh_get("/mesh/peers")``
    passes it at argument index 0, so every call site in the Telegram mesh handlers — the
    exact #1050 surface this guard was written for — was silently skipped while the guard
    reported a clean run over 28 other sites.
    """
    helpers: dict[str, tuple[int, int | None, str | None]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in node.args.args]
        if params and params[0] in ("self", "cls"):
            params = params[1:]
        if "path" not in params:
            continue
        referenced = {
            n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(node)
            if isinstance(n, (ast.Name, ast.Attribute))
        }
        if referenced & _GATEWAY_BASE_SYMBOLS:
            method_idx = params.index("method") if "method" in params else None
            helpers[node.name] = (
                params.index("path"),
                method_idx,
                None if method_idx is not None else _helper_verb(node.name),
            )
    return helpers


def _requested_paths(
    files: list[tuple[Path, str, ast.Module]],
) -> list[tuple[str, int, str, str, str | None]]:
    """(file, line, how, path, verb) for each statically-known gateway request.

    ``verb`` is ``None`` when the method cannot be resolved statically — the verb check
    skips those rather than guessing, on the same "permissive when unsure" rule the path
    side follows.
    """
    found: list[tuple[str, int, str, str, str | None]] = []
    for f, _text, tree in files:
        helpers = _gateway_helpers(tree)
        # `gateway_request` is the canonical helper; a module that names it uses it.
        if any(
            isinstance(n, ast.Name) and n.id == "gateway_request"
            or isinstance(n, ast.Attribute) and n.attr == "gateway_request"
            for n in ast.walk(tree)
        ):
            helpers.setdefault("gateway_request", (1, 0, None))

        rel = f.relative_to(CORE).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)

            if name in helpers:
                idx, midx, fixed_verb = helpers[name]
                # Positional, or `path=` by keyword — a caller may spell it either way,
                # and reading only positionals makes the keyword form invisible.
                arg = next(
                    (kw.value for kw in node.keywords if kw.arg == "path"),
                    node.args[idx] if len(node.args) > idx else None,
                )
                if arg is not None:
                    pattern = _static_path(arg)
                    if pattern and pattern.startswith("/"):
                        verb = fixed_verb
                        if midx is not None:
                            m = next(
                                (kw.value for kw in node.keywords if kw.arg == "method"),
                                node.args[midx] if len(node.args) > midx else None,
                            )
                            verb = (
                                str(m.value).upper()
                                if isinstance(m, ast.Constant) and isinstance(m.value, str)
                                else None
                            )
                        found.append((rel, node.lineno, f"{name}()", pattern, verb))
                continue

            # Direct construction: session.get(f"{gateway_base_url()}/status")
            if name in _WEB_VERBS or name == "request":
                for arg in node.args[:2]:
                    if not isinstance(arg, ast.JoinedStr):
                        continue
                    inner = {
                        n.id if isinstance(n, ast.Name) else n.attr
                        for v in arg.values
                        for n in ast.walk(v)
                        if isinstance(n, (ast.Name, ast.Attribute))
                    }
                    if not inner & _GATEWAY_BASE_SYMBOLS:
                        continue
                    pattern = _static_path(arg)
                    if pattern is None:
                        continue
                    # Strip the leading base-URL interpolation to leave the path.
                    tail = pattern.lstrip(_WILDCARD)
                    if tail.startswith("/"):
                        verb = name.upper() if name in _WEB_VERBS else None
                        found.append((rel, node.lineno, f"{name}(f-string)", tail, verb))
    return found


def test_every_requested_gateway_route_is_served() -> None:
    files = _candidate_files()
    routes = _served_routes(files)
    assert len(routes) >= _MIN_ROUTES, (
        f"only {len(routes)} gateway routes resolved (expected >= {_MIN_ROUTES}). "
        "The registration idiom probably changed — a served-set that collapses to empty "
        "makes this guard report every caller as broken."
    )

    matchers = [(_to_regex(r), verbs) for r, verbs in routes.items()]
    requested = _requested_paths(files)
    assert len(requested) >= _MIN_CALL_SITES, (
        f"only {len(requested)} gateway call sites resolved (expected >= {_MIN_CALL_SITES}). "
        "Helper discovery probably stopped matching — an empty requested-set passes this "
        "guard while checking nothing."
    )

    resolved = sum(1 for *_rest, verb in requested if verb)
    assert resolved >= _MIN_VERB_RESOLUTION * len(requested), (
        f"only {resolved}/{len(requested)} call sites had a resolvable HTTP method "
        f"(floor {_MIN_VERB_RESOLUTION:.0%}). The verb check skips what it cannot "
        "resolve, so this silently degrades into a path-only guard."
    )
    routes_with_verb = sum(1 for v in routes.values() if v)
    assert routes_with_verb >= _MIN_VERB_RESOLUTION * len(routes), (
        f"only {routes_with_verb}/{len(routes)} routes had a resolvable method "
        f"(floor {_MIN_VERB_RESOLUTION:.0%}). A route with no verb is treated as "
        "accepting ANY method — enough of those and the verb check stops biting."
    )

    unserved: list[tuple[str, int, str, str]] = []
    wrong_verb: list[tuple[str, int, str, str, str, list[str]]] = []
    for f, line, how, path, verb in requested:
        base = path.split("?")[0]
        if base in _ALLOWED_UNSERVED:
            continue
        hits = [verbs for m, verbs in matchers if m.match(base)]
        if not hits:
            unserved.append((f, line, how, base))
            continue
        # The path exists. Does anything serving it accept this method? A route whose
        # method could not be resolved contributes an EMPTY verb set, read as "any".
        if verb and all(hits) and not any(verb in v for v in hits):
            wrong_verb.append((f, line, how, base, verb, sorted({x for v in hits for x in v})))

    assert not unserved, (
        "These callers request a gateway route nothing serves — a guaranteed 404 that "
        "arrives as 'the subsystem is unavailable' (#1050):\n"
        + "\n".join(f"  {f}:{line}  {how} -> {path}" for f, line, how, path in sorted(unserved))
        + "\n\nEither register the route, point the caller at one that exists, or record "
        "it in _ALLOWED_UNSERVED with the reason it is served elsewhere."
    )

    assert not wrong_verb, (
        "These callers use a method the route does not accept — a guaranteed 405, which "
        "reaches the user identically to the 404 above (an exception inside a `try:` "
        "reported as 'unavailable'). 335 of the gateway's routes accept exactly one "
        "method, so this is the same defect wearing a different status code:\n"
        + "\n".join(
            f"  {f}:{line}  {how} -> {verb} {path}  (serves: {', '.join(served)})"
            for f, line, how, path, verb, served in sorted(wrong_verb)
        )
    )


def test_allowed_unserved_entries_are_still_requested() -> None:
    """An exemption for a call site that no longer exists is stale — say so."""
    if not _ALLOWED_UNSERVED:
        return
    live = {path.split("?")[0] for _, _, _, path, _v in _requested_paths(_candidate_files())}
    stale = sorted(set(_ALLOWED_UNSERVED) - live)
    assert not stale, (
        "_ALLOWED_UNSERVED names paths no caller requests any more — remove them:\n"
        + "\n".join(f"  {p}  ({_ALLOWED_UNSERVED[p]})" for p in stale)
    )


# ── The docs are a consumer of this contract too ──────────────────────────────
#
# The code check above cannot see a README. `/mesh/topology` survived it for exactly
# that reason: `docs/guides/mesh-multi-machine.md` told users to
# `curl http://localhost:8789/mesh/topology` for a "topology report with SPOF analysis"
# and listed it in its API table, while the route was never registered —
# `mesh/router.get_topology_report()` was complete, tested, and called by nothing.
# A user following the guide got a 404; nothing in the build disagreed with the guide.
#
# The gateway's port is the scoping signal, and it is IMPORTED rather than typed: a doc
# URL on another port belongs to another service, and three of them do. Measured across
# both doc trees: 12 URLs on the gateway port, 3 on :8091 (the operational-factory
# `tool-gateway`, which genuinely serves its `/flow/*` routes), 1 on :8090, 1 on :9090.
# Scoping by "localhost" alone reported all five as broken gateway routes.

def _gateway_port() -> int:
    from navig._daemon_defaults import _GATEWAY_PORT

    return int(_GATEWAY_PORT)


def _documented_gateway_urls() -> list[tuple[str, int, str]]:
    """(doc file, line, path) for every `http://localhost:<gateway port>/…` in the docs."""
    pattern = re.compile(
        r"https?://(?:localhost|127\.0\.0\.1):" + str(_gateway_port())
        + r"(/[A-Za-z0-9_\-/{}.]*)"
    )
    found: list[tuple[str, int, str]] = []
    for root in _DOC_ROOTS:
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if ":" + str(_gateway_port()) not in text:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for m in pattern.finditer(line):
                    found.append((f.relative_to(REPO).as_posix(), lineno, m.group(1)))
    return found


def test_documented_gateway_urls_name_routes_that_exist() -> None:
    """A doc that tells a user to curl the gateway must name a route it serves."""
    files = _candidate_files()
    routes = _served_routes(files)
    assert len(routes) >= _MIN_ROUTES, (
        f"only {len(routes)} routes resolved — see the code check above."
    )
    matchers = [_to_regex(r) for r in routes]

    documented = _documented_gateway_urls()
    assert documented, (
        "no gateway URLs found in the docs at all — either both doc trees moved or the "
        f"port changed away from {_gateway_port()}. This check silently guards nothing "
        "when its scan comes back empty."
    )

    missing = [
        (f, line, path)
        for f, line, path in documented
        if not any(m.match(path.rstrip("/") or "/") for m in matchers)
    ]

    assert not missing, (
        "These docs tell a user to curl a gateway route that does not exist — the user "
        "gets a 404 and the build says nothing (#1050 on the surface people actually "
        "follow):\n"
        + "\n".join(f"  {f}:{line}  {path}" for f, line, path in missing)
        + "\n\nEither register the route or point the doc at one that exists."
    )
