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

| Variable                    | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SECRET_KEY`                | Django secret key                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `WEBHOOK_SECRET_KEY`        | Auth webhook HMAC key                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `BETTER_AUTH_URL`           | Internal URL to auth service                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `DB_*`                      | PostgreSQL connection                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `AWS_*`                     | MinIO/S3 connection                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `CELERY_*`                  | Redis broker/backend config                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `CINS_SKIP_HIDDEN_SHEETS`   | Exclude hidden/veryHidden Excel worksheets from commission extraction (default `true`; set `false` to roll back without a redeploy)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `CINS_CODEGEN_GATE`         | Codegen completeness gate: reconcile emitted Pass-3 rules against the source grid's rate-bearing rows; a dropped table/segment retries then flags `requires_review` (default `true`; `false` disables). Tunables: `CINS_CODEGEN_COVERAGE_FLOOR_RATIO`, `CINS_CODEGEN_COVERAGE_MIN_EXPECTED`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `CINS_STATE_RTO_MANDATORY`  | State and RTO must be non-empty on every exported deal. A rule with no state raises a HIGH issue, which blocks the CICS output-cache pin; an RTO that would export blank raises MEDIUM (non-blocking), because a product resolving to no RTO bucket may simply be a non-motor line (default `true`; `false` disables without a redeploy and logs a warning)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `CINS_REGRESSION_GUARD`     | Cross-run regression guard: a cache-miss re-extraction that lost a product/segment vs the last good run of the same document is warned + not cached (default `true`). Tunables: `CINS_REGRESSION_GUARD_RATIO`, `CINS_REGRESSION_GUARD_MIN_TOTAL`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `CINS_PCV_TAXI_MIRROR`      | Mirror CV/PCV rules whose `vehicle_sub_class` is exactly `taxi` into a duplicate `Private Car` / `Taxi` product, so both deals export (default `true`; `false` disables without a redeploy). Turning it off after a document was cached makes the `private car\|taxi` segment key disappear and trips `CINS_REGRESSION_GUARD`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `CINS_CHOLA_MMV`            | Chola make/model canonicalization + validation. Folds a rule's free-text `vehicle_make`/`vehicle_model` (and their `_exclude` twins) onto the spelling seeded in `CarrierVehicleMMVMaster`, and raises a MEDIUM (non-blocking) finding for values no vehicle matches. ONE flag gates both halves — disabling only the fold would leave the validator flagging spellings it would have fixed. **Default `false`.** Measured against a dump of 2,237 live Chola deals the fold is non-destructive but marginal: it changes 737 of ~1.34M make values, all corporate-suffix tidy-ups, while half the makes those deals use are absent from the master. Turning it ON changes CICS output — bump `CICS_PIPELINE_VERSION` to 1.7.0 and confirm `CINS_REGRESSION_GUARD` at the same time                                                                                                                              |
| `CINS_MAKE_MODEL_MANDATORY` | `VehicleMake` and `VehicleModel` must be non-empty on every exported Chola deal; a rule carrying neither an include nor an `_exclude` list raises a MEDIUM (non-blocking) finding per product, with counts. Deliberately **not** HIGH: a HIGH blocks the CICS output-cache pin, and on a real Chola grid 43.5% of rules carry no make and 97.5% no model — most of it source-faithful (the Private Car grid has no MAKE/MODEL column; the CV grid prices them only on up-to-3.5T rows), so a HIGH would wall every Chola document out of the cache. Separate flag from `CINS_CHOLA_MMV` because that one governs the MMV master while this governs an export rule. **Default `false`:** a dump of 2,237 live Chola deals carries no `VehicleModel` on 93% of them (100% of Car/Taxi) and no make on 8, so blank is the shape the client's own production system ships and the rule would fire on every document |
| `NUXT_PUBLIC_*`             | Frontend runtime config                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `SERVER_NAME`               | Nginx server name (prod)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `PROXY_PORT`                | Nginx external port (default 8080)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

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

**The default branch is `dev`. There is no `master` and no `main`** — the remote carries
neither, and `origin/HEAD` points at `dev`. Branch off `dev`, diff against `origin/dev`,
and open every PR with `gh pr create --base dev`. Tooling that guesses a default (and
Claude Code's own session preamble, which reports "master") is wrong here; verify with
`git ls-remote --heads origin` rather than assuming.

## Git Hooks

- Husky pre-commit hooks configured (`.husky/`)
- Backend: pre-commit via `.pre-commit-config.yaml` (ruff, etc.)
- Never use `--no-verify` to skip hooks

## Releases

Managed by `release-it` with GitHub releases auto-generated.
