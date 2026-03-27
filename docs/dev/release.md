# Release Checklist

This checklist is for NAVIG maintainers preparing an official release.

## Pre-Release

- [ ] **Update version number** in:
  - `pyproject.toml`
  - `navig/__init__.py` (if applicable)
  - Any other version references
- [ ] **Update CHANGELOG.md** with release notes:
  - New features
  - Bug fixes
  - Breaking changes (if any)
  - Security updates
- [ ] **Run full test suite**:
  ```bash
  pytest tests/ -v
  ```
- [ ] **Run linters**:
  ```bash
  flake8 navig/
  black --check navig/
  ```
- [ ] **Test installation** on clean environment:
  ```bash
  pip install -e .
  navig --version
  ```
- [ ] **Review security** considerations (credentials, input validation)

## Build

- [ ] **Create build artifacts**:
  ```bash
  python -m build
  ```
- [ ] **Generate SHA256 checksums**:
  ```bash
  sha256sum dist/* > dist/checksums.txt
  ```
- [ ] **Test installation from wheel**:
  ```bash
  pip install dist/navig-*.whl
  ```

## Publish

- [ ] **Create Git tag**:
  ```bash
  git tag -a v2.x.x -m "Release v2.x.x"
  ```
- [ ] **Push tag to GitHub**:
  ```bash
  git push origin v2.x.x
  ```
- [ ] **Create GitHub Release**:
  - Use tag as release name
  - Copy changelog section to release notes
  - Attach build artifacts (`.tar.gz`, `.whl`)
  - Attach `checksums.txt`
- [ ] **Publish to PyPI** (if configured):
  ```bash
  twine upload dist/*
  ```
- [ ] **Update website** download links (if applicable)

## Post-Release

- [ ] **Announce release**:
  - GitHub Discussions
  - Discord (if available)
  - Social media (if applicable)
- [ ] **Monitor for issues**:
  - Check GitHub Issues for installation problems
  - Watch for security reports
- [ ] **Update documentation** if needed:
  - Installation instructions
  - Breaking change migration guides

## Hotfix Process

For critical bug fixes or security patches:

1. Create hotfix branch from release tag: `git checkout -b hotfix/v2.x.y v2.x.x`
2. Apply minimal fix
3. Update version to patch increment (e.g., `2.1.0` → `2.1.1`)
4. Follow full release checklist above
5. Merge hotfix back to `main`

---

**Next Release**: TBD
