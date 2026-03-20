# GitHub Manual & CLI Steps — navig-run/core

_Covers all GitHub settings and automation that cannot be committed as files._
_Labels: `DONE_LOCALLY` | `READY_TO_RUN_WITH_GH` | `MANUAL_IN_GITHUB_UI` | `BLOCKED`_

---

## Branch Protection — `main`

**Label:** `READY_TO_RUN_WITH_GH`

**Requires:** `gh` CLI authenticated with `repo` scope or admin access to `navig-run`.

```bash
bash scripts/github-bootstrap.sh
```

**What it configures:**
- 1 required approving review, stale reviews dismissed, CODEOWNERS enforced
- Required status checks: `secret-scan`, `test (3.10)`, `test (3.11)`, `test (3.12)` (strict — branch must be up-to-date)
- `enforce_admins: true` — applies to maintainer too
- Force pushes disabled
- Branch deletion disabled

**Verify:**
```bash
gh api repos/navig-run/core/branches/main/protection \
  --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count}'
```

---

## Enable Discussions on `navig-run/core`

**Label:** `MANUAL_IN_GITHUB_UI`

**Navigation:**
1. `https://github.com/navig-run/core`
2. Settings → General → Features
3. Toggle **Discussions** on

**Expected outcome:** "Discussions" tab appears on the repo. No further configuration needed until categories are set up (see Discussions Bootstrap section).

---

## Enable Private Vulnerability Reporting

**Label:** `MANUAL_IN_GITHUB_UI`

**Navigation:**
1. `https://github.com/navig-run/core`
2. Security tab → Advisories
3. Click **Enable private vulnerability reporting**

**Expected outcome:** "Report a vulnerability" button appears for users on the Security tab. Reports go to maintainer privately without public disclosure.

---

## Verify Security Advisories Are Enabled

**Label:** `READY_TO_RUN_WITH_GH`

```bash
gh api repos/navig-run/core \
  --jq '.security_and_analysis'
```

**Expected output should include:**
```json
{
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}
}
```

Note: Private vulnerability reporting status is not exposed via this API endpoint. Confirm via UI after enabling.

---

## Create Org `.github` Repo (if absent)

**Label:** `READY_TO_RUN_WITH_GH`

The org `.github` directory exists locally at `K:\_PROJECTS\navig\.github\` but the corresponding `navig-run/.github` repository on GitHub may not exist yet. Check first:

```bash
gh repo view navig-run/.github 2>&1
```

If it returns a 404, create it:

```bash
gh repo create navig-run/.github \
  --public \
  --description "navig-run org profile and shared community health files"
```

Then push the local org profile content:

```bash
cd "K:\_PROJECTS\navig"
git -C .github init
git -C .github add -A
git -C .github commit -m "chore: add org profile README"
git -C .github remote add origin https://github.com/navig-run/.github.git
git -C .github push -u origin main
```

---

## Push Org Profile README

**Label:** `READY_TO_RUN_WITH_GH`

After the `navig-run/.github` repo exists, push the scaffold created at `K:\_PROJECTS\navig\.github\profile\README.md`.

The `profile/README.md` path is required — GitHub reads org profile READMEs only from that exact path.

---

## Pin `core` Repo on Org Profile

**Label:** `MANUAL_IN_GITHUB_UI`

**Navigation:**
1. `https://github.com/navig-run`
2. Click **Customize your organization** (or the edit pencil)
3. Under **Pinned repositories** → **Pin repositories**
4. Select `core` → Save

**Expected outcome:** `core` appears pinned at the top of the org page.

---

## Fix Sponsor Button (FUNDING.yml)

**Label:** `DONE_LOCALLY`

Fixed in this branch. `.github/FUNDING.yml` was updated from `REPLACE_ME` placeholders to:
```yaml
github: navig-run
custom: ["https://navig.run/support"]
```

---

## Discussions Bootstrap

**Label for all items:** `MANUAL_IN_GITHUB_UI`

**Prerequisite:** Discussions must be enabled first (see section above).

**Navigation for all:** `https://github.com/navig-run/core/discussions` → Manage categories

### Recommended categories

| Category | Type | Purpose |
|----------|------|---------|
| **Announcements** | Announcement | Maintainer-only: releases, breaking changes, deprecations. Locks non-maintainers from posting. |
| **Ideas** | Open-ended discussion | Pre-issue feature exploration. Reduces premature Issues for half-formed requests. |
| **Q&A** | Question / Answer | Usage questions, command help. The single biggest issue-noise reducer. |
| **Show and Tell** | Open-ended discussion | Community operator workflows, use cases, deployment patterns, integrations. |

**For each category:**
1. Click **New category** (or edit the default ones GitHub creates)
2. Set the name, discussion format, and emoji as desired
3. For **Announcements**: set format to "Announcement" — this restricts posting to maintainers

**Note:** GitHub creates a default set of categories (General, Ideas, Q&A, Show and Tell, Polls) when Discussions is first enabled. Delete or rename the defaults to match the above four. Delete **General** (too broad, creates noise) and **Polls** (not needed at this stage).
