# insure_iq

Full-stack monorepo: Django 5 backend + Nuxt 4 (Vue 3) frontend.
Reusable starter template — copy per project, don't modify shared tooling.

## Stack

- **Backend**: Django 5.2 + DRF + Celery + PostgreSQL 17 + Redis 7
- **Frontend**: Nuxt 4 + Vue 3 + Tailwind CSS 4 + Pinia + Nuxt UI 3
- **Auth**: Better Auth (external service: `rhobotsai/auth:latest`)
- **Storage**: MinIO (S3-compatible)
- **Proxy**: Nginx (dev + prod)
- **Package manager**: pnpm 10 (workspaces) — never use npm or yarn

## Project Layout

| Path | Purpose |
| --- | --- |
| `backend/` | Django app — config, accounts, core, system apps |
| `backend/config/` | settings.py, urls.py, celery.py, authentication.py |
| `backend/accounts/` | User/auth models and views |
| `backend/core/` | Core business logic app |
| `web/` | Nuxt frontend |
| `web/app/pages/` | Nuxt file-based routes |
| `web/app/components/` | Vue components |
| `web/app/composables/` | Vue 3 composables |
| `web/app/stores/` | Pinia stores |
| `conf/` | Nginx dev/prod configs |
| `scripts/` | Helper shell scripts |
| `postgres/` | DB init SQL scripts |

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
- Postgres: port 5432, DB `insure_iq`

## Environment Variables

Copy `.env.example` → `.env` at project root. Key vars:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django secret key |
| `WEBHOOK_SECRET_KEY` | Auth webhook HMAC key |
| `BETTER_AUTH_URL` | Internal URL to auth service |
| `DB_*` | PostgreSQL connection |
| `AWS_*` | MinIO/S3 connection |
| `CELERY_*` | Redis broker/backend config |
| `NUXT_PUBLIC_*` | Frontend runtime config |
| `SERVER_NAME` | Nginx server name (prod) |
| `PROXY_PORT` | Nginx external port (default 8080) |

## Git Hooks

- Husky pre-commit hooks configured (`.husky/`)
- Backend: pre-commit via `.pre-commit-config.yaml` (ruff, etc.)
- Never use `--no-verify` to skip hooks

## Releases

Managed by `release-it` with GitHub releases auto-generated.
