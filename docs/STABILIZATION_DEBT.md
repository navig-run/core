# Stabilization Debt Handoff

This document captures technical debt, deferred verification work, and test-coverage gaps identified during the current stabilization pass so follow-up work can be prioritized without re-deriving context.

## Known Gaps and Deferred Items

### 1. UFW / Fail2Ban Test Coverage

- Status: Validated via mocks and static code-path analysis only.
- Gap: No assertions have been executed against a live or containerized host.
- Required follow-up: Add an integration test that provisions a minimal Linux environment, such as a Docker container with UFW and Fail2Ban installed, and asserts real rule application and log-ban behavior end-to-end.
- Done when: `pytest -m integration` passes against the containerized host with zero mocked network calls for UFW and Fail2Ban paths.

### 2. `tests/test_autonomous_agent.py` Non-Hermetic Smoke Test

- Status: Skips gracefully when `http://localhost:8789` is unhealthy.
- Gap: Provides no hermetic coverage; test results are environment-dependent.
- Required follow-up:
  1. Extract the business logic under test into a mockable interface.
  2. Replace live gateway calls with a contract-tested stub.
  3. Retain the live smoke test as an optional `@pytest.mark.live` gate, excluded from CI by default.
- Done when: The test file passes in a clean CI environment with no external services running.

### 3. `pyproject.toml` setuptools License Metadata Deprecation

- Status: Non-breaking; `python -m build` emits deprecation warnings only.
- Gap: Will become a hard failure on a future setuptools cutover.
- Required follow-up: Migrate the `license` field to SPDX expression format per [PEP 639](https://peps.python.org/pep-0639/).

```toml
# Replace legacy form:
license = { text = "MIT" }

# With SPDX expression:
license = "MIT"
license-files = ["LICENSE"]
```

- Done when: `python -m build 2>&1 | grep -i deprecat` returns no output.

### 4. Unaudited Pre-Existing Changes in Worktree

- Status: The full test suite passes against the current aggregate state.
- Gap: Changes outside this stabilization pass were not audited line-by-line.
- Required follow-up:
  1. Run `git diff {{baseline_ref}} HEAD -- . ':(exclude){{stabilization_scope_paths}}'` to isolate unrelated changes.
  2. Review each file diff for unintended side effects.
  3. If any change modifies public API, runtime behavior, or security surface, open a dedicated PR for review before the next release cut.
- Done when: Every changed file outside the stabilization scope is either approved or reverted.

## Prioritization

| # | Item | Risk | Effort | Priority |
|---|---|---|---|---|
| 3 | setuptools deprecation | Low (time-bomb) | XS | Fix next PR |
| 2 | Hermetic agent tests | Medium | S | Current sprint |
| 1 | UFW and Fail2Ban integration tests | High | M | Current sprint |
| 4 | Unaudited worktree changes | High (unknown) | M-L | Before next release |
