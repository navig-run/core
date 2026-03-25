"""
defender_exclusion worker — manage Windows Defender scan exclusions via PowerShell MpPreference.
CLI path: sys defender exclude path | remove | list | process
"""
from __future__ import annotations
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, is_admin

TOOL = "defender_exclusion"
PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

def _check():
    if current_os() != "windows":
        return "defender_exclusion is Windows-only."
    return None

def _ps(script: str, dry: bool = False) -> tuple[int, str, str]:
    return run([*PS, script], timeout=20, dry_run=dry)

def cmd_add_path(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "add-path", [e], ms=t.ms())
    dry = args.get("dry_run", False)
    if not dry and not is_admin():
        return err(TOOL, "add-path", ["Admin required to modify Defender exclusions."], ms=t.ms())
    try:
        path = args["path"]
        rc, out, er = _ps(f'Add-MpPreference -ExclusionPath "{path}"', dry)
        if rc != 0 and not dry:
            return err(TOOL, "add-path", [er or out], ms=t.ms())
        return ok(TOOL, "add-path", {"path": path, "added": not dry, "dry_run": dry}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "add-path", [str(e)], ms=t.ms())

def cmd_remove_path(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "remove-path", [e], ms=t.ms())
    dry = args.get("dry_run", False)
    if not dry and not is_admin():
        return err(TOOL, "remove-path", ["Admin required."], ms=t.ms())
    try:
        path = args["path"]
        rc, out, er = _ps(f'Remove-MpPreference -ExclusionPath "{path}"', dry)
        if rc != 0 and not dry:
            return err(TOOL, "remove-path", [er or out], ms=t.ms())
        return ok(TOOL, "remove-path", {"path": path, "removed": not dry, "dry_run": dry}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "remove-path", [str(e)], ms=t.ms())

def cmd_list(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "list", [e], ms=t.ms())
    try:
        script = (
            "$p = Get-MpPreference; "
            "ConvertTo-Json @{"
            "  paths=(if($p.ExclusionPath){$p.ExclusionPath}else{@()});"
            "  processes=(if($p.ExclusionProcess){$p.ExclusionProcess}else{@()});"
            "  extensions=(if($p.ExclusionExtension){$p.ExclusionExtension}else{@()})"
            "} -Compress"
        )
        rc, out, er = _ps(script)
        if rc != 0:
            return err(TOOL, "list", [er], ms=t.ms())
        try:
            data = json.loads(out)
        except Exception:
            data = {"raw": out}
        return ok(TOOL, "list", data, ms=t.ms())
    except Exception as e:
        return err(TOOL, "list", [str(e)], ms=t.ms())

def cmd_add_process(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "add-process", [e], ms=t.ms())
    dry = args.get("dry_run", False)
    if not dry and not is_admin():
        return err(TOOL, "add-process", ["Admin required."], ms=t.ms())
    try:
        proc = args["process"]
        rc, out, er = _ps(f'Add-MpPreference -ExclusionProcess "{proc}"', dry)
        if rc != 0 and not dry:
            return err(TOOL, "add-process", [er or out], ms=t.ms())
        return ok(TOOL, "add-process", {"process": proc, "added": not dry, "dry_run": dry}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "add-process", [str(e)], ms=t.ms())

HANDLERS = {"add-path": cmd_add_path, "remove-path": cmd_remove_path,
            "list": cmd_list, "add-process": cmd_add_process}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try:
            req = json.loads(line)
            handler = HANDLERS.get(req.get("command",""))
            emit(handler(req.get("args",{})) if handler
                 else err(TOOL, req.get("command","?"), ["Unknown command"]))
        except Exception as e:
            emit(err(TOOL, "?", [str(e)]))

if __name__ == "__main__":
    main()
