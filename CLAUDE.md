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

| Path                         | Purpose                                                                                                                                                                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `backend/`                   | Django project root                                                                                                                                                                                                            |
| `backend/config/`            | settings.py, urls.py, celery.py, authentication.py                                                                                                                                                                             |
| `backend/accounts/`          | User/auth models and views                                                                                                                                                                                                     |
| `backend/core/`              | Shared business logic                                                                                                                                                                                                          |
| `backend/commission_intake/` | Commission statement ingestion pipeline (models, tasks, serializers, views)                                                                                                                                                    |
| `backend/policy_extract/`    | Policy data extraction app (models, services, tasks)                                                                                                                                                                           |
| `backend/cins/`              | CINS product field mapping, schema, prompts, processors, validators                                                                                                                                                            |
| `backend/master_data/`       | Carrier master tables (RTO zones, product hierarchy, vehicle MMV) + `services/resolver.py` (RTO/product bucket lookups), `services/mmv.py` (make/model matcher), seed CSVs in `data/`, carrier upload API in `views/upload.py` |
| `backend/system/`            | System-level models and views                                                                                                                                                                                                  |
| `web/`                       | Nuxt frontend                                                                                                                                                                                                                  |
| `web/app/pages/`             | Nuxt file-based routes (incl. `commission-intake/`, `policy-extract.vue`)                                                                                                                                                      |
| `web/app/components/`        | Vue components                                                                                                                                                                                                                 |
| `web/app/composables/`       | Vue 3 composables                                                                                                                                                                                                              |
| `web/app/stores/`            | Pinia stores                                                                                                                                                                                                                   |
| `web/app/api/`               | Frontend HTTP client (`http.ts`)                                                                                                                                                                                               |
| `web/app/middleware/`        | Nuxt route middleware                                                                                                                                                                                                          |
| `web/app/layouts/`           | Nuxt layouts                                                                                                                                                                                                                   |
| `conf/`                      | Nginx dev/prod configs                                                                                                                                                                                                         |
| `scripts/`                   | Helper shell scripts                                                                                                                                                                                                           |
| `postgres/`                  | DB init SQL scripts                                                                                                                                                                                                            |

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

## Master Data

Reference tables (RTO zones, product hierarchy, vehicle Make/Model/Variant) live in
`backend/master_data/`. Two ways in, sharing the same parsers (`services/chola_parsers.py`)
and loader upserts, so both are idempotent and neither ever deletes a row:

```bash
# Seed / re-seed from the committed CSVs in master_data/data/ (the normal route)
docker compose exec backend python manage.py load_master_data

# Regenerate the derived Chola MMV seed CSV after replacing a source .xlsx, then commit it
# (`--out PATH` writes elsewhere; `load_master_data --file PATH` overrides the workbook)
docker compose exec backend python manage.py export_chola_mmv_csv
```

The other route is the authenticated carrier upload API,
`POST /api/master-data/carrier-uploads/` (multipart: `carrier`, `master_type` one of
`rto_zone|product_hierarchy|mmv`, `file` .xlsx). The `mmv` type parses ~22k rows
synchronously in the request thread — prefer the CSV seed path for full books.

## Environment Variables

Copy `.env.example` → `.env` at project root. Key vars:

| Variable             | Purpose                                                                                      |
| -------------------- | -------------------------------------------------------------------------------------------- |
| `SECRET_KEY`         | Django secret key                                                                            |
| `WEBHOOK_SECRET_KEY` | Auth webhook HMAC key                                                                        |
| `BETTER_AUTH_URL`    | Internal URL to auth service                                                                 |
| `DB_*`               | PostgreSQL connection                                                                        |
| `AWS_*`              | MinIO/S3 connection                                                                          |
| `CELERY_*`           | Redis broker/backend config                                                                  |
| `POLICY_EXTRACT_*`   | policy_extract worker tuning (concurrency, task time limits, mem limit) — see `.env.example` |
| `REDIS_MAXMEMORY`    | Redis broker memory cap (dev compose; `noeviction`)                                          |
| `MINERU_BACKEND`     | MinerU `backend` form field; flip only in lockstep with the server cutover                   |
| `CINS_*`             | CINS pipeline flags — see the table below                                                    |
| `NUXT_PUBLIC_*`      | Frontend runtime config                                                                      |
| `SERVER_NAME`        | Nginx server name (prod)                                                                     |
| `PROXY_PORT`         | Nginx external port (default 8080)                                                           |

### CINS pipeline flags

Every one is a no-redeploy rollback switch. This table is an **index** — name,
default, one-line effect. The rationale (why the default is what it is, what
broke, what a flip costs) belongs in `.claude/rules/cins.md`; read it there
before flipping anything, and put new rationale there rather than here.

`CINS_CODEGEN_GATE`, `CINS_REGRESSION_GUARD` and `CINS_PCV_TAXI_MIRROR` have no
rule bullet yet — the first two are only referenced in passing by the ones that
do, and the mirror's rationale lives in the `pcv_taxi_mirror.py` module
docstring. Write them up there when you next touch any of them.

| Variable                        | Default | Effect                                                                                                                                                    |
| ------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CINS_SKIP_HIDDEN_SHEETS`       | `true`  | Hidden/veryHidden Excel worksheets are indexed and classified but never feed extraction                                                                   |
| `CINS_CODEGEN_GATE`             | `true`  | Reconcile emitted Pass-3 rules against the grid's rate-bearing rows; a dropped table/segment retries, then flags `requires_review`                        |
| `CINS_STATE_RTO_MANDATORY`      | `true`  | Every exported deal must carry a State (missing → HIGH, blocks the cache pin) and an RTO (blank → MEDIUM)                                                 |
| `CINS_REGRESSION_GUARD`         | `true`  | A cache-miss re-extraction that lost a product/segment vs the last good run of the same document is warned and not cached                                 |
| `CINS_PCV_TAXI_MIRROR`          | `true`  | CV/PCV rules whose `vehicle_sub_class` is exactly `taxi` are mirrored into a duplicate `Private Car` / `Taxi` product so both deals export                |
| `CINS_GCV_NO_SUBCLASS`          | `true`  | GCV rules carry no `vehicle_sub_class`, so the `VehicleSubClass` column exports blank                                                                     |
| `CINS_RENEWAL_IMPLIES_ROLLOVER` | `true`  | A commercial-vehicle rule whose `business_type` contains `renewal` also carries `rollover`, so `BusinessType` exports `renewal,rollover`                  |
| `CINS_MISCD_SUBCLASS_MERGE`     | `true`  | MISC-D keeps Agricultural Tractor and Harvester as their own rules; every other body of a source rule collapses into one multi-value rule                 |
| `CINS_CHOLA_MMV`                | `false` | Fold Chola `vehicle_make`/`vehicle_model` onto the `CarrierVehicleMMVMaster` spelling, and flag (MEDIUM) values no vehicle matches. One flag, both halves |
| `CINS_MAKE_MODEL_MANDATORY`     | `false` | `VehicleMake`/`VehicleModel` must be non-empty on every exported Chola deal; a rule with neither raises a MEDIUM per product                              |

Tunables, all read only when their gate is on: `CINS_CODEGEN_COVERAGE_FLOOR_RATIO`,
`CINS_CODEGEN_COVERAGE_MIN_EXPECTED`, `CINS_REGRESSION_GUARD_RATIO`,
`CINS_REGRESSION_GUARD_MIN_TOTAL`.

Flipping `CINS_PCV_TAXI_MIRROR`, `CINS_GCV_NO_SUBCLASS`,
`CINS_RENEWAL_IMPLIES_ROLLOVER`, `CINS_MISCD_SUBCLASS_MERGE`, `CINS_CHOLA_MMV`
or `CINS_MAKE_MODEL_MANDATORY` changes CICS output — treat it as a
`CICS_PIPELINE_VERSION` change and confirm `CINS_REGRESSION_GUARD` at the same
time.

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

## Branching

Two long-lived branches, and **`dev` is the integration branch — never open a PR against
`main`.** `origin/HEAD` points at `dev`.

| Branch | Role                                                                                                              |
| ------ | ----------------------------------------------------------------------------------------------------------------- |
| `dev`  | Integration / default. Branch off it, diff against `origin/dev`, and open every PR with `gh pr create --base dev` |
| `main` | **Production deployment branch.** Advanced by promoting `dev`, not by feature PRs                                 |

There is no `master`. Claude Code's session preamble reports `master` as the main branch,
which is wrong on both counts — the branch does not exist, and the repo's PR target is
`dev` regardless. Do not trust a tool's guess at the default here.

## Git Hooks

- Husky pre-commit hooks configured (`.husky/`)
- Backend: pre-commit via `.pre-commit-config.yaml` (ruff, etc.)
- Never use `--no-verify` to skip hooks

## Releases

Managed by `release-it` with GitHub releases auto-generated.
