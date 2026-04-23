# AGENTS.md

## Dev Commands

```bash
pnpm dev              # Start full stack (installs deps + docker compose up)
pnpm dev:down         # Stop
pnpm dev:prune        # Stop + delete volumes (data loss)
pnpm db:reset         # Reset postgres databases
pnpm init             # Seed a dev user
```

## Service URLs (dev)

- Backend API: `http://localhost:8000/api/health/`
- Frontend: `http://localhost:5173`
- Auth: `http://localhost:10000`
- MinIO console: `http://localhost:9901`
- Postgres: port 5432, DB `insur_iq`

## Key Constraints

- **Package manager**: pnpm only. Never use npm or yarn.
- **Node**: version 24
- **Python**: >=3.12
- **Pre-commit**: Backend uses `.pre-commit-config.yaml` (ruff, black, mypy, isort, flake8). Run `pre-commit install` in `backend/`.
- **Git hooks**: Husky pre-commit hooks enforced. Never use `--no-verify`.

## Architecture Notes

- Backend entry: `backend/config/`
- Frontend entry: `web/app/pages/`
- Django settings: `backend/config/settings.py`
- Auth is external (Better Auth container at `rhobotsai/auth:latest`), talks to backend via webhook.

## Testing

Run backend tests:
```bash
cd backend
python -m pytest
```

Run frontend tests:
```bash
cd web
pnpm test
```

## Releases

Uses `release-it`. GitHub releases auto-generated.