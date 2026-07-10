# Releasing & CI

How insur_iq versions, builds, and ships container images. This covers **CI + release +
ECR image-push**. It does NOT deploy — pulling the pushed image into ECS/EC2 is a separate,
project-specific step (see "Deploy" at the bottom).

## Branch model

| Branch    | Role                                                                         |
| --------- | ---------------------------------------------------------------------------- |
| `dev`     | Default / integration branch. Feature branches PR into `dev`.                |
| `master`  | Stable production branch. Merge `dev` → `master` via PR when stable.         |
| tags `v*` | Cut **only from `master`** by `pnpm release`. Tags drive the released image. |

## CI (`.github/workflows/ci.yml`)

On every PR and on pushes to `dev`/`master` (and `v*` tags):

- **lint** (diff-scoped) — runs the backend `pre-commit` suite (ruff + ruff-format + mypy +
  codespell + hygiene hooks) and web eslint on **only the files changed in the PR/push**, not
  the whole repo. The codebase predates linting, so this enforces the standard on new/changed
  code and grandfathers legacy debt until a file is next touched. The local husky hook is
  likewise staged-files-only, so the two stay consistent.
- **test** — postgres + redis service containers, then Django boot gate
  (`manage.py check`, `makemigrations --check`), an informational `check --deploy`, the full
  backend suite via **`python manage.py test`** (the runner all backend tests are written for —
  not pytest), and `pnpm --filter web test`.
- **push-ecr** (pushes/tags only, after lint+test) — builds `linux/amd64` images (provenance +
  SBOM) and pushes to ECR. **Prod is amd64-only.** Self-skips until AWS is wired
  (`if: vars.AWS_REGION != ''`), so a repo without AWS config shows no red X.

Tag map:

| Trigger          | Image tags               |
| ---------------- | ------------------------ |
| push to `dev`    | `dev`, `dev-<sha>`       |
| push to `master` | `master`, `master-<sha>` |
| tag `vX.Y.Z`     | `X.Y.Z`, `latest`        |

## One-time AWS setup (keyless OIDC → ECR)

> **Owner: <assign a maintainer with AWS admin creds>. Target date: <fill in>.**
> Until this runs, `push-ecr` self-skips and no image is published. Do not assume "CI is green"
> means "images are shipping" — check that this step is done.

Run the shipped script (creates the OIDC provider, an IAM role scoped to this repo's
`master`/`dev`/tags, the ECR-push policy, and both ECR repos — all idempotent), then run the
`gh` commands it prints:

```bash
ORG=rhobots-ai REPO=insur-iq AWS_REGION=ap-south-1 \
ECR_BACKEND_REPO=insur-iq/backend ECR_WEB_REPO=insur-iq/web \
./infra/aws/setup-aws.sh
```

It prints the role ARN and the exact commands to register:

- Variables: `AWS_REGION`, `ECR_BACKEND_REPO`, `ECR_WEB_REPO`
- Secret: `AWS_ROLE_ARN`

If any is unset, the `push-ecr` preflight step fails fast with the fix command instead of an
opaque OIDC error.

## Branch protection (do this AFTER the first CI run)

GitHub rejects a `required_status_checks` context (`CI`) it has never seen, so push once to
trigger the workflow **before** applying protection:

```bash
gh repo edit --default-branch dev
gh api -X PUT "repos/{owner}/{repo}/branches/master/protection" \
  -f 'required_status_checks={"strict":true,"contexts":["CI"]}' \
  -F enforce_admins=true \
  -f 'required_pull_request_reviews={}' \
  -F restrictions=
```

## Cutting a release

Releases use [`release-it`](https://github.com/release-it/release-it). Run **from `master`**
(`git.requireBranch: master` enforces this):

```bash
export GITHUB_TOKEN=$(gh auth token)   # release-it needs this to create the GitHub release
git checkout master && git pull
pnpm release            # interactive: choose patch/minor/major
# or non-interactively:
pnpm release minor --ci
```

`pnpm release` bumps the version in `package.json`, `backend/package.json`, and `web/package.json`
in lockstep, commits `chore: release vX.Y.Z`, tags `vX.Y.Z`, pushes, and opens a GitHub release
with auto-generated notes. The tag push triggers the ECR build & push.

Dry run anytime (no commit/push): `pnpm release --dry-run --ci`.

**Version baseline.** The repo is seeded at `1.4.0` (root/backend/web `package.json`), with an
annotated tag `v1.4.0` on the seed commit so release-it computes the next version from `1.4.x`,
not from `1.0.0`. Verify with `pnpm release --dry-run --ci`.

> **`--no-verify` exception.** release-it commits/pushes with `--no-verify` (its config). The
> repo rule "never use `--no-verify`" applies to hand commits; this is the one documented,
> automated exception (release commits only touch version files already validated by the bumper).

## Deploy (out of scope here)

The pipeline gets a versioned `linux/amd64` image into ECR. Wiring the deploy target
(ECS / EC2 / compose-pull) to pull `latest` (released) or `master` (staging) per environment is
a separate, unowned follow-up. `scripts/build-and-push.sh` (manual, multi-arch) stays for local
builds and disaster recovery.
