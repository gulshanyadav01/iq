# InsurIQ (Monorepo)

Full-stack app for insurance commission intake and policy data extraction.

- **Backend**: Django 5.2 + DRF + Celery + PostgreSQL 17 + Redis 7
- **Frontend**: Nuxt 4 (Vue 3) + Tailwind CSS 4 + Pinia + Nuxt UI 3
- **Auth**: Better Auth (external container, webhook to backend)
- **Storage**: MinIO (S3-compatible) in dev; S3 in prod
- **Proxy**: Nginx (single entrypoint on `:8080` in dev)
- **Package manager**: pnpm 10 (workspaces) — never use npm or yarn

## Monorepo structure

| Path         | Purpose                                                                                                                                     |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/`   | Django project (`config/`, `accounts/`, `core/`, `commission_intake/`, `policy_extract/`, `cins/`, `master_data/`, `pipelines/`, `system/`) |
| `web/`       | Nuxt 4 frontend (`app/pages`, `app/components`, `app/stores`, …)                                                                            |
| `conf/`      | Nginx dev/prod configs                                                                                                                      |
| `postgres/`  | DB init SQL (`insur_iq`, `auth`)                                                                                                            |
| `scripts/`   | Helper scripts (env gen, build/push, db reset, nuke)                                                                                        |
| `infra/aws/` | One-time AWS OIDC → ECR wiring (`setup-aws.sh` + policies)                                                                                  |
| `.github/`   | CI workflow + dependabot                                                                                                                    |

## Local setup (development)

Prereqs: Docker, Node 24, pnpm 10.x.

```bash
# 1. Config: generate a workspace .env (random free ports + secrets overlay)
pnpm setup:env            # or: cp .env.example .env

# 2. Install workspace tooling + git hooks
pnpm i                    # runs husky install + warms the pre-commit cache

# 3. Start the full dev stack (all services via Docker Compose)
pnpm dev
```

Everything is served through the Nginx proxy on one port (default `8080`):

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8080/service-api`
- Auth: `http://localhost:8080/auth`
- MinIO console: `http://localhost:9090`
- Postgres: port `5432`, DB `insur_iq`

Seed a dev user (optional): `pnpm init`

Stop: `pnpm dev:down` · Reset DB only: `pnpm db:reset` · Nuke volumes: `pnpm dev:prune`

## Contributing

### Git hooks (pre-commit)

`pnpm i` installs a Husky `pre-commit` hook that is **path-gated**: it lints `web/` only when
web files are staged, and runs the backend `pre-commit` suite only when `backend/` files are
staged.

The backend suite is a Python tool (`pre-commit`). Because this repo runs entirely in Docker,
host Python is not guaranteed — so the hook **self-detects a runner** (`pre-commit` on PATH →
`uvx pre-commit` → `pipx run pre-commit`) and, if none is found, prints an install hint and
skips the backend block rather than blocking your commit.

To get the backend lint locally, install `pre-commit` once (no repo change needed):

```bash
uv tool install pre-commit     # or: pipx install pre-commit
```

**Never** use `git commit --no-verify` to get around a missing tool — install it instead
(see `CLAUDE.md`).

### Lint & test manually

```bash
# Frontend
cd web && pnpm lint && pnpm test

# Backend (inside the container, where Python deps live)
docker compose exec backend python manage.py test
docker compose exec backend pre-commit run --all-files   # or via uvx on the host
```

## CI / Releasing

CI runs on every PR and on pushes to `dev`/`master`: lint → test (`python manage.py test` +
web) → docker build. On pushes/tags it also builds and pushes `linux/amd64` images to AWS ECR
(keyless OIDC), once AWS is wired.

Branch model, one-time AWS setup, branch protection, and `pnpm release` are documented in
[`RELEASING.md`](./RELEASING.md).

## License

MIT
