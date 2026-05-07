---
name: Provider identification pipeline
description: How insurance provider is identified for a commission_intake document — and the gap that lets garbage values through
type: project
---

Provider name on a `CommissionDocument` is set during Pass 1 (metadata extraction) of the CINS pipeline:

- LLM is prompted with `pass1_metadata_prompt.md` and instructed: "Extract exactly as written. If not present → return empty string."
- The output (`MetadataExtraction.provider_name`, `extraction_passes.py:241`) is copied verbatim into `RawProviderInfo.name` (`merge_passes`, `extraction_passes.py:570`).
- `RawToCICSTransformer._transform_provider` (`raw_to_cics_transformer.py:556`) sets `provider_id = slugify(name).upper()` — no lookup, no normalization.
- The Celery task `run_extraction` (`commission_intake/tasks.py:312-316`) writes `cics_doc.provider.provider_name` back to `CommissionDocument.provider`.

**Why this fails:** there is no constrained set of insurance carriers anywhere — not on the model (`provider = CharField(max_length=255, blank=True)` with no choices), not in the schema, not in a master table, and not in the prompt. Whatever string the LLM returns is accepted and stored. If the document doesn't textually contain the carrier name (e.g., a broker forwards an email and only the broker's domain is visible), the LLM can return the broker, an email domain, or any other plausible string.

**Why:** Original design assumed the document body would name the carrier explicitly; this assumption breaks for forwarded/broker-relayed emails where the carrier name only appears in headers (Subject, To, Cc) that aren't fed into Pass 1.

**How to apply:** When investigating "wrong provider" tickets, check (a) whether `primary_content` actually mentions the carrier, (b) whether the carrier name is buried in stripped headers (`email_context` in DB has Subject/To/From), and (c) whether the LLM extracted whatever was textually present. The fix is to constrain Pass 1 to a known carrier list and/or feed Subject/To/Cc into Pass 1 — not to "fix the LLM."
