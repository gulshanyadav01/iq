# insur_iq

Full-stack monorepo: Django 5 backend + Nuxt 4 (Vue 3) frontend.
Domain: insurance commission intake and policy data extraction.

## Stack

- **Backend**: Django 5.2 + DRF + Celery + PostgreSQL 17 + Redis 7
- **Frontend**: Nuxt 4 + Vue 3 + Tailwind CSS 4 + Pinia + Nuxt UI 3
- **Auth**: Better Auth (external service: `rhobotsai/auth:latest`)
- **Storage**: MinIO (S3-compatible)
- **Proxy**: Nginx (dev + prod)
- **Package manager**: pnpm 10 (workspaces) — never use npm or yarn

## Project Layout

| Path                         | Purpose                                                                     |
| ---------------------------- | --------------------------------------------------------------------------- |
| `backend/`                   | Django project root                                                         |
| `backend/config/`            | settings.py, urls.py, celery.py, authentication.py                          |
| `backend/accounts/`          | User/auth models and views                                                  |
| `backend/core/`              | Shared business logic                                                       |
| `backend/commission_intake/` | Commission statement ingestion pipeline (models, tasks, serializers, views) |
| `backend/policy_extract/`    | Policy data extraction app (models, services, tasks)                        |
| `backend/cins/`              | CINS product field mapping, schema, prompts, processors, validators         |
| `backend/system/`            | System-level models and views                                               |
| `web/`                       | Nuxt frontend                                                               |
| `web/app/pages/`             | Nuxt file-based routes (incl. `commission-intake/`, `policy-extract.vue`)   |
| `web/app/components/`        | Vue components                                                              |
| `web/app/composables/`       | Vue 3 composables                                                           |
| `web/app/stores/`            | Pinia stores                                                                |
| `web/app/api/`               | Frontend HTTP client (`http.ts`)                                            |
| `web/app/middleware/`        | Nuxt route middleware                                                       |
| `web/app/layouts/`           | Nuxt layouts                                                                |
| `conf/`                      | Nginx dev/prod configs                                                      |
| `scripts/`                   | Helper shell scripts                                                        |
| `postgres/`                  | DB init SQL scripts                                                         |

## Dev Workflow

```bash
# Start full dev stack (all services via Docker Compose)
pnpm dev

# Stop
pnpm dev:down

# Nuke everything (volumes too)
pnpm dev:prune

# Reset DB only
pnpm db:reset
```

Services in dev:

- Backend API: `http://localhost:8080/service-api`
- Frontend: `http://localhost:8080`
- Auth: `http://localhost:8080/auth`
- MinIO console: `http://localhost:9090`
- Postgres: port 5432, DB `insur_iq`

## Environment Variables

Copy `.env.example` → `.env` at project root. Key vars:

| Variable                   | Purpose                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SECRET_KEY`               | Django secret key                                                                                                                                                                                                                                                                                                                                           |
| `WEBHOOK_SECRET_KEY`       | Auth webhook HMAC key                                                                                                                                                                                                                                                                                                                                       |
| `BETTER_AUTH_URL`          | Internal URL to auth service                                                                                                                                                                                                                                                                                                                                |
| `DB_*`                     | PostgreSQL connection                                                                                                                                                                                                                                                                                                                                       |
| `AWS_*`                    | MinIO/S3 connection                                                                                                                                                                                                                                                                                                                                         |
| `CELERY_*`                 | Redis broker/backend config                                                                                                                                                                                                                                                                                                                                 |
| `CINS_SKIP_HIDDEN_SHEETS`  | Exclude hidden/veryHidden Excel worksheets from commission extraction (default `true`; set `false` to roll back without a redeploy)                                                                                                                                                                                                                         |
| `CINS_CODEGEN_GATE`        | Codegen completeness gate: reconcile emitted Pass-3 rules against the source grid's rate-bearing rows; a dropped table/segment retries then flags `requires_review` (default `true`; `false` disables). Tunables: `CINS_CODEGEN_COVERAGE_FLOOR_RATIO`, `CINS_CODEGEN_COVERAGE_MIN_EXPECTED`                                                                 |
| `CINS_STATE_RTO_MANDATORY` | State and RTO must be non-empty on every exported deal. A rule with no state raises a HIGH issue, which blocks the CICS output-cache pin; an RTO that would export blank raises MEDIUM (non-blocking), because a product resolving to no RTO bucket may simply be a non-motor line (default `true`; `false` disables without a redeploy and logs a warning) |
| `CINS_REGRESSION_GUARD`    | Cross-run regression guard: a cache-miss re-extraction that lost a product/segment vs the last good run of the same document is warned + not cached (default `true`). Tunables: `CINS_REGRESSION_GUARD_RATIO`, `CINS_REGRESSION_GUARD_MIN_TOTAL`                                                                                                            |
| `NUXT_PUBLIC_*`            | Frontend runtime config                                                                                                                                                                                                                                                                                                                                     |
| `SERVER_NAME`              | Nginx server name (prod)                                                                                                                                                                                                                                                                                                                                    |
| `PROXY_PORT`               | Nginx external port (default 8080)                                                                                                                                                                                                                                                                                                                          |

## Testing

```bash
# Backend — Django TestCase, run inside the container
docker compose exec -T backend python manage.py test
docker compose exec -T backend python manage.py test cins.tests.test_ncb_tier -v 2   # one module

# Frontend — vitest
cd web && pnpm test
```

Repo-wide `pnpm lint` is red on arrival (pre-existing violations in legacy files) and CI
lints diff-scoped only, so lint just the files you changed:
`cd web && pnpm exec eslint <paths>`.

## Git Hooks

- Husky pre-commit hooks configured (`.husky/`)
- Backend: pre-commit via `.pre-commit-config.yaml` (ruff, etc.)
- Never use `--no-verify` to skip hooks

## Releases

Managed by `release-it` with GitHub releases auto-generated.
