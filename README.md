## InsurIQ (Monorepo)

Starter template for a full‑stack app: Django backend + Vue 3 (Vite + Tailwind) frontend. Designed to be copied and adapted without modifying the underlying tooling and infra.

## 🚀 Quick Setup

**First time using this template?** Run the setup script to customize it for your project:

```bash
# Make setup script executable (if needed)
chmod +x setup.sh

# Run the interactive setup
./setup.sh
```

The setup script will:
- Ask for your project name, database name, bucket name, etc.
- Replace all template placeholders with your values
- Create a backup of original files
- Update all configuration files automatically

**Manual setup alternative:** See the [Manual Setup](#manual-setup) section below.

### Highlights
- **Backend**: Django 5 + DRF, Celery-ready, Redis, Postgres, S3-compatible storage (MinIO), Spectacular (OpenAPI)
- **Frontend**: Vue 3 + Vite + Tailwind 4
- **Auth**: External auth service (container) with webhook to backend
- **Infra**: Docker Compose for dev, MinIO, Redis, Postgres
- **Tooling**: pnpm workspaces, turbo-ready, release-it, Husky hooks

### Monorepo structure
- `backend/` — Django app, dev Dockerfile, entrypoint
- `web/` — Vue app with Vite + Tailwind
- `postgres/` — DB bootstrap (`auth`, `insure_iq`)
- `docker-compose.dev.yml` — full dev stack
- `scripts/` — helper scripts (`init-db-user.sh`, `nuke.sh`)

---

### Quick start (development)
Prereqs: Docker, Node 24, pnpm 10.x.

1) Install workspace tooling
```bash
corepack enable && corepack prepare pnpm@10.15.0 --activate
pnpm i
```

2) Start the dev stack
```bash
pnpm run dev
```

3) Wait for healthchecks, then open:
- Backend API: `http://localhost:8000/api/health/`
- Web app: `http://localhost:5173`
- Auth service: `http://localhost:10000`
- MinIO console: `http://localhost:9901`

4) Seed a dev user (optional)
```bash
bash ./scripts/init-db-user.sh
```

5) Stop the stack
```bash
pnpm run infra:dev:down
```

Reset volumes (danger):
```bash
pnpm run infra:dev:prune
```

---

### Services (dev compose)
- `backend`: Django (migrations auto-run). Ports: 8000
- `web`: Vite dev server for Vue. Ports: 5173
- `auth`: External auth (webhook to backend). Ports: 10000
- `postgres`: Postgres DB with init scripts. Ports: 5432
- `redis`: Redis cache/broker. Ports: 6379
- `minio`: S3-compatible storage. Ports: 9900 (API), 9901 (console)

Environment variables are wired in `docker-compose.dev.yml`. Defaults are provided; override via a `.env` file or shell.

---

## Manual Setup

### Rename checklist (after copying the repo)
# Linux: sed -i 's/insure_iq/your_app/g'

# Kebab-case rename
grep -rl "insure-iq" . | xargs sed -i '' -e 's/insure-iq/your-app/g' # macOS
# Linux: sed -i 's/insure-iq/your-app/g'
```

Backend Django project/module rename: If you change the Django project module name under `backend/`, also update imports and `DJANGO_SETTINGS_MODULE` where relevant. Current project module is under `backend/config` and apps under `backend/*/apps.py`.

---

### Running parts individually
Backend inside compose (default):
```bash
pnpm run infra:dev:up
```

Frontend only (outside Docker):
```bash
cd web
pnpm i
pnpm run dev
```

Backend only (outside Docker, requires Python 3.12+ and Postgres running):
```bash
cd backend
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

---

### Environment variables (common)
Key defaults (see `docker-compose.dev.yml` for full list):
- Backend: `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DB_*`, `AWS_*`, `CELERY_*`
- Web (build args): `VITE_AUTH_BASE_URL`, `VITE_API_BASE_URL`
- Auth: `BETTER_AUTH_*`, `TRUSTED_ORIGINS`, `DATABASE_STRING`, `WEBHOOK_*`

---

### Conventions
- Package manager: `pnpm`
- Node engine: `24`
- Python: `>=3.12`
- Codegen: optional OpenAPI client under `web/web/src/api`

---

### Common tasks
```bash
# Bring up dev stack
pnpm run infra:dev:up

# Tear down
pnpm run infra:dev:down

# Reset with volumes
pnpm run infra:dev:prune

# Reset Postgres databases (auth + app)
pnpm run db:reset

# Nuke repo artifacts (script-defined)
pnpm run nuke
```

---

### License
MIT


