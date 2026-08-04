from django.db import models

from config.abstract_models import TimeStampedUUIDModel


class CicsOutputCache(TimeStampedUUIDModel):
    """Deterministic output cache for a fully-extracted document.

    Pass-2/3 codegen re-rolls a fresh LLM parser program on every run, so the same
    byte-identical input can yield a different (sometimes segment-dropping) output.
    This cache pins the *whole extraction output* — the CICS doc plus every
    ExtractionPass row needed to repopulate the UI — keyed by everything that can
    change the result: the source content, the indexer version, the prompt corpus,
    the LLM provider/model, and the deterministic pipeline version.

    It lives in its OWN table, NOT on ``ExtractionPass``/``CICSResult``, because
    ``run_extraction`` deletes those rows at the top of every run (see
    ``tasks.run_extraction``) — a cache stored there would be wiped before it could
    ever hit.

    Only gate-passing, non-``requires_review`` results are cached (see
    ``cics_cache.should_cache``) so a reproducibly-wrong program is never frozen in.

    Lifecycle: rows are never evicted here — a version bump writes a NEW key and the
    prior-version row is deliberately kept so ``find_prior_baseline`` can diff against
    it (the regression guard). Growth is bounded per document to one row per
    (version tuple); across many documents × bumps this table grows monotonically.
    Prune out of band if it ever matters (e.g. keep latest N per ``content_hash``).
    """

    # SHA256 over (content_hash, indexer_version, prompt_version_hash,
    # llm_provider, llm_model, pipeline_version). unique=True already indexes it.
    cache_key = models.CharField(max_length=64, unique=True)

    # Key components, stored denormalised for debugging / invalidation queries.
    content_hash = models.CharField(max_length=128, db_index=True)
    indexer_version = models.CharField(max_length=16)
    prompt_version_hash = models.CharField(max_length=64)
    pipeline_version = models.CharField(max_length=32)
    llm_provider = models.CharField(max_length=64, blank=True, default="")
    llm_model = models.CharField(max_length=128, blank=True, default="")

    # Replayable payload — everything needed to rebuild the UI without an LLM call.
    cics_doc = models.JSONField()
    warnings = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    duration_seconds = models.FloatField(default=0)
    sheet_results = models.JSONField(default=list)
    # The per-pass entry dicts consumed by tasks._upsert_extraction_passes.
    sheet_pass_results = models.JSONField(default=list)
    # Resolved provider short-code written back to the document on a hit.
    provider_id = models.CharField(max_length=128, blank=True, default="")

    # How many times this cached result was served (observability for E4).
    hit_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CicsOutputCache({self.cache_key[:12]}… hits={self.hit_count})"
