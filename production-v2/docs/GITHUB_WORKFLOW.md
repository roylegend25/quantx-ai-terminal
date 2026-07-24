# GitHub Workflow (Phase 5)

```
feature/*  ──▶  main (Development VM)  ──▶  testing  ──▶  production-v2 (Production VM)
   │                    │                       │                  │
   │  PR + review       │  auto-deployed to      │  manual promote  │  manual promote
   │                    │  the existing dev VM   │  after smoke     │  only via deploy
   │                    │  (current box,         │  passes on       │  scripts, never
   └── never merges     │  unchanged workflow)   │  testing         │  by hand
       straight to                                                  │
       production-v2                                                ▼
                                                              Never developed on
                                                              directly. Ever.
```

## Branches

- **`main`** — active development. This is the existing VM's branch today; nothing about its
  workflow changes. The old VM becomes *Development* going forward, per this project's directive
  — same branch, same deploys, just a relabeled role.
- **`testing`** — a short-lived integration branch. Cut from `main` when a set of changes is ready
  to leave development; deployed to a Testing VM (a second, disposable VM or the dev VM's staging
  slot — sizing/creation is out of scope here, same recipe as `production-v2` with a cheaper
  machine type). Exists to catch anything environment-specific (SQLite-only, no Postgres; the
  trimmed nginx routes) before it reaches money-affecting infrastructure.
- **`production-v2`** — what the Production V2 VM's `deploy.sh` actually clones (`DEPLOY_BRANCH` in
  `.env`). Fast-forwarded from `testing` only after a deliberate promotion step, never committed to
  directly, never rebased.

## Rules

1. **Never develop directly on Production.** No `git commit` on the VM, no editing files over SSH,
   no `docker exec`-ing a fix into a running container. If something needs to change, it changes in
   `main`, flows through `testing`, then gets promoted — even for a one-line hotfix (see
   `docs/ROLLBACK_GUIDE.md` for what to do if production is actively broken *right now*, which is
   the one case where rolling back to a previous image beats waiting for a forward fix).
2. **Production only receives code via `deploy.sh` / the update script**, never `git pull` run ad
   hoc by a person on the box. This keeps `APP_GIT_SHA`/`APP_IMAGE_TAG` tagging, the pre-deploy
   backup, and the health-gated cutover in `docs/UPDATE_GUIDE.md` from ever being skipped.
3. **Promotion is a fast-forward merge, not a new commit.** `testing → production-v2` and
   `main → testing` should both be `git merge --ff-only`; if that fails, the branches have
   diverged and the fix is to re-cut `testing` from `main`, not to force-merge.

## Promotion commands (run from your workstation, not the VMs)

```bash
# main -> testing, once a batch of changes is ready to leave dev
git fetch origin
git checkout testing
git merge --ff-only origin/main
git push origin testing
# ... deploy to the Testing VM, run the smoke checklist in docs/OPERATIONS.md ...

# testing -> production-v2, only after testing passes
git checkout production-v2
git merge --ff-only origin/testing
git push origin production-v2
# ... SSH to the Production V2 VM and run the update flow, see docs/UPDATE_GUIDE.md ...
```

## What lives where

- `production-v2/` (this folder) is committed to `main` today so it ships to every branch by
  default — the Production V2 VM's `deploy.sh` only ever reads it after checking out the
  `production-v2` branch, so nothing here affects the existing dev VM's `docker-compose.yml`/`.env`
  at the repo root.
- Root-level `docker-compose.yml`/`.env`/`nginx/default.conf` (the current dev setup) are untouched
  by any of this — they keep running the Development VM exactly as they do today.
