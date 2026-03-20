# Next Actions — navig-run/core GitHub Bootstrap

## Best Next Action

Push `chore/github-bootstrap` to `navig-run/core` and open a PR to get the fixed files into `main` before running the branch protection script (protection will block direct pushes to main once applied).

---

## Checklist

- [x] Branch `chore/github-bootstrap` created
- [x] `feature_request.yml` — Prompt Optimization Checklist removed
- [x] `config.yml` — Security policy URL fixed (`navig-core/` prefix removed)
- [x] `FUNDING.yml` — `REPLACE_ME` replaced with `github: navig-run` + correct support URL
- [x] `SUPPORT.md` created
- [x] `BOOTSTRAP_REPORT.md` created
- [x] `scripts/github-bootstrap.sh` created
- [x] `GITHUB_MANUAL_OR_CLI_STEPS.md` created
- [x] `K:\_PROJECTS\navig\.github\profile\README.md` created (org profile)
- [ ] Push `chore/github-bootstrap` to `navig-run/core`
- [ ] Open PR → merge to `main`
- [ ] Run `bash scripts/github-bootstrap.sh` (requires `gh` auth with repo/admin scope)
- [ ] Verify protection: `gh api repos/navig-run/core/branches/main/protection --jq '{checks: .required_status_checks.contexts}'`
- [ ] Create `navig-run/.github` repo on GitHub (if absent): `gh repo create navig-run/.github --public`
- [ ] Push org `.github/profile/README.md` to `navig-run/.github` repo
- [ ] Enable Discussions: Settings → General → Features → Discussions
- [ ] Set up Discussion categories (Announcements, Ideas, Q&A, Show and Tell) — delete General and Polls
- [ ] Enable private vulnerability reporting: Security → Advisories
- [ ] Pin `core` repo on org profile: `github.com/navig-run` → Customize org → Pin

---

## Manual GitHub UI Steps

| Step | Navigation |
|------|-----------|
| Enable Discussions | `github.com/navig-run/core` → Settings → General → Features → toggle Discussions |
| Set Discussion categories | `github.com/navig-run/core/discussions` → Manage categories |
| Enable private vuln reporting | `github.com/navig-run/core` → Security tab → Advisories → Enable |
| Pin `core` on org profile | `github.com/navig-run` → Customize organization → Pin repositories |

---

## `gh` Commands

In execution order — run after `main` is updated:

```bash
# 1. Check auth
gh auth status

# 2. Apply branch protection (runs after PR is merged so main is protected correctly)
bash scripts/github-bootstrap.sh

# 3. Verify protection
gh api repos/navig-run/core/branches/main/protection \
  --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, force_push: .allow_force_pushes.enabled}'

# 4. Check if org .github repo exists
gh repo view navig-run/.github 2>&1

# 5. If 404 above, create it:
gh repo create navig-run/.github --public --description "navig-run org profile and shared community health files"

# 6. Verify secret scanning status
gh api repos/navig-run/core --jq '.security_and_analysis'
```
