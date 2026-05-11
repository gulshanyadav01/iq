# Architecture Review — `insure_iq` backend

Synthesized from a 3-lens critical review (Django/DRF, Celery/pipeline, data/persistence). Findings are ranked by urgency, not by app.

The codebase has a request/handler layer and a Celery layer, but no domain layer in between. State machines, file I/O, LLM calls, and S3 keys all live wherever they were first written.

---

## Tier 1 — dangerous in production now

1. **Celery picks up rows before the API commits.** `enqueue_processing_chain(...)` is called inline in views with no `transaction.on_commit` ([commission_intake/views/commission_document.py:64,119,145,178](backend/commission_intake/views/commission_document.py#L64), [policy_extract/views/policy_extraction_request.py:68](backend/policy_extract/views/policy_extraction_request.py#L68)). With `policy_extract` concurrency=10, the worker `.get(id=...)` races the commit → `_fail_document` marks a doc FAILED that's about to exist.
2. **`PolicyExtractionRequestViewSet` is `AllowAny`** ([policy_extract/views/policy_extraction_request.py:33](backend/policy_extract/views/policy_extraction_request.py#L33)) plus `CORS_ALLOW_ALL_ORIGINS = True` ([config/settings.py:310](backend/config/settings.py#L310)). Unauthenticated upload + list + retrieve, internet-wide.
3. **Two writers create `User` rows with no idempotency** — JWT backend ([config/authentication/__init__.py:51-67](backend/config/authentication/__init__.py#L51)) and the webhook ([auth_webhook.py:74-82](backend/config/authentication/auth_webhook.py#L74)). Webhook HMAC `compare_digest`s against possibly-`None` header → `TypeError` before 401. JWT errors are `print()`'d and downgraded to anonymous.
4. **`exec()` runs in a daemon thread inside the Celery worker** ([code_gen_executor.py:411-425](backend/cins/processors/code_gen_executor.py#L411)). Threads can't be killed; AST blocklist is bypassable via comprehensions. Bad LLM output → worker OOM.

---

## Tier 2 — silent data corruption / observability

5. **Every stage swallows errors with `raise Ignore()`** ([commission_intake/tasks.py:190,328,502,859](backend/commission_intake/tasks.py#L190), [policy_extract/tasks.py:137](backend/policy_extract/tasks.py#L137)) — Celery doesn't record these as failures. No retry, no DLQ, no Flower count. Every transient flake = permanent FAILED.
6. **No Celery hardening.** [config/celery.py](backend/config/celery.py) is the stock template — no `acks_late`, `reject_on_worker_lost`, time limits, or `task_routes`. [entrypoint.sh:26](backend/entrypoint.sh#L26) starts the worker with no `-Q`, consuming the unused `celery` queue while real work is on `commission_intake`/`policy_extract`.
7. **State machine smeared across 6 tasks + the column.** Status writers run outside the work that produced the result — worker death between them = stale `INDEXING` while next stage runs. Six view actions duplicate the same in-flight check.
8. **`derive_pipeline_routing` called from 3 sites** ([pipeline.py:63,525,798](backend/cins/processors/pipeline.py#L63)) and pre-created `PENDING ExtractionPass` rows depend on them agreeing. `unique_together` empty-string defaults collide email-only and sheet-only docs on `("","")`.
9. **`rerun_transform` is non-idempotent** ([commission_intake/tasks.py:803-851](backend/commission_intake/tasks.py#L803)) — list-concats `commission_entries`/`master_data` with no dedup. UI double-click = duplicates.

---

## Tier 3 — schema rot

10. **No `Carrier` table.** A 20-entry tuple in [cins/schema/carriers.py:30](backend/cins/schema/carriers.py#L30) drives the whole pipeline; `provider` is a free `CharField`. Adding a carrier needs a redeploy. Docstring says 25, tuple has 20.
11. **Unbounded JSONFields with no `schema_version`**: `cics_doc`, `sheet_contents`, `result`, `metadata`, `index_progress`, `Setting.setting`, plus `Profile` is literally empty. Products and rules live inside `cics_doc["products"]` — unqueryable.
12. **`content_hash` has no unique constraint** ([models/indexed_content.py:25](backend/commission_intake/models/indexed_content.py#L25)). Same email re-uploaded = full re-extraction.
13. **S3 keys tracked via `JSONField(default=list)`** ([models/indexed_content.py:23](backend/commission_intake/models/indexed_content.py#L23)). Mid-loop crash in `_clear_prior_results` → permanent orphans. No GC.
14. **Soft-delete wired everywhere, used nowhere**, while every FK is `CASCADE`. `deleted_at` has no index — implicit `WHERE deleted_at IS NULL` on every query is a future seq-scan.
15. **`SIMPLE_JWT` 365-day config references an `identity` app that doesn't exist in `INSTALLED_APPS`** ([config/settings.py:228-258](backend/config/settings.py#L228)). `accounts/signals.py` listens to the wrong `User` model — never fires.

---

## Tier 4 — layering

16. **Fat ViewSet** ([commission_intake/views/commission_document.py](backend/commission_intake/views/commission_document.py), 559 lines) — openpyxl in `export_excel`, fresh boto3 client per `download`.
17. **Serializers do I/O.** [policy_extract serializer:14-29](backend/policy_extract/serializers/policy_extraction_request.py#L14) writes to S3 in `create()`. [accounts user serializer:19,33](backend/accounts/serializers/user.py#L19) sends SMTP synchronously.
18. **Function-local imports in 6 places** in `commission_document.py` — circular-dep workarounds.
19. **`ThreadPoolExecutor(max_workers=4)` inside a Celery task** ([pipeline.py:1031](backend/cins/processors/pipeline.py#L1031)) — shared non-unique temp filenames stomp each other; threads leak DB connections.
20. **`CommissionDocumentSerializer` eagerly nests `cics_result` + `indexed_content` + all `extraction_pass`** with no `prefetch_related` — N+1 + multi-MB responses.
21. **Synchronous `requests.post(timeout=30)` callback inside `_set_completed_status`** ([policy_extract/tasks.py:35](backend/policy_extract/tasks.py#L35)) with task-level retry=3 → callback fires up to 3× to a receiver with no idempotency key.

---

## Highest-leverage fixes

1. Wrap every `apply_async` in `transaction.on_commit`.
2. Replace `raise Ignore()` with `autoretry_for=(TransientError,)`; add `acks_late` + time limits + `-Q` queue scoping in `entrypoint.sh`.
3. Move the FSM out of views into `commission_intake/services/state.py`; atomic `UPDATE ... WHERE status IN (...)` instead of read-then-write.
4. Make `Carrier` a real table; validate Pydantic against `Carrier.objects.values_list('slug', flat=True)`.

Tier 1 is exploitable or data-loss-prone today. Tier 3 compounds every month — every new JSONField is a migration you can't write later.
