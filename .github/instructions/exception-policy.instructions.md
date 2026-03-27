## Exception-handling policy

When you encounter `except: pass`, `except Exception: pass`, or any empty `except` block:

1. **Do not leave silent exception swallowing in place** unless it is clearly intentional and harmless.
2. **Replace broad exceptions with the narrowest real exception types** you can infer from the code.
3. If the exception is intentionally ignored, do one of these:
   - replace with `contextlib.suppress(SpecificError)`
   - or keep the `except` but add a short comment explaining why ignoring it is correct
   - or log at `debug` level if the failure may matter during diagnosis
4. **Never silently swallow**:
   - `Exception`
   - bare `except:`
   - `ImportError` for required dependencies
   - file/network/database/session errors that can hide broken behavior
5. **Usually acceptable to ignore only when clearly intentional**:
   - `KeyboardInterrupt` during shutdown
   - `asyncio.CancelledError` during task cancellation
   - cleanup-only best-effort removal like unlink/delete/close, but only with a narrow exception
6. For optional imports, do **not** use `except ImportError: pass`.
   - set a sentinel like `web = None`
   - or set `HAS_X = False`
   - or raise a clear runtime error where the feature is actually used
7. For parsing loops, use narrow exceptions like `ValueError` and add a comment such as `# malformed line; ignore`.
8. Add or update tests when behavior changes.
9. Do not change behavior casually in shutdown/control-flow paths.
10. After fixes, run lint and tests.

Goal: **no silent swallowed exceptions without justification**.