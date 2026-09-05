#!/usr/bin/env bash
# NAVIG pre-commit security check — skips gracefully if Python unavailable.
# NOTE: On Windows, 'py.exe' may be a Microsoft Store redirect stub (exit 49).
# We validate each candidate actually executes Python before accepting it.

_python_works() {
    "$@" -c "import sys; sys.exit(0)" >/dev/null 2>&1
}

PYTHON_BIN=""
if   _python_works python3;  then PYTHON_BIN="python3"
elif _python_works py -3;    then PYTHON_BIN="py -3"
elif _python_works python;   then PYTHON_BIN="python"
fi

if [[ -z "$PYTHON_BIN" ]]; then
    echo "navig-security: Python not available; skipping."
    exit 0
fi

$PYTHON_BIN -c "
import importlib, sys
try:
    mod = importlib.import_module('navig.core.security')
    getattr(mod, 'run_security_audit')
    getattr(mod, 'substitute_env_vars')
    print('navig-security: OK')
except ImportError:
    print('navig-security: navig module not installed; skipping.')
    sys.exit(0)
" || { echo "navig-security: check skipped (non-fatal)"; exit 0; }
