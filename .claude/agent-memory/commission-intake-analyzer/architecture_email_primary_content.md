---
name: Email primary_content scope (what Pass 1 actually sees)
description: For .eml documents, Pass 1 only sees From + Date + cleaned body — Subject/To/Cc are stripped before the LLM sees them
type: project
---

In `cins/processors/content/indexer.py::_index_email`, the email body that becomes `primary_content` (= `content.md` = Pass 1 input via `build_pass_contents` in `pipeline.py:202`) is built as:

```
From: {email_data.from}
Date: {email_data.date}

{cleaned_body}
```

**Subject is NOT included. To/Cc/Bcc are NOT included.** Subject/From/Date are persisted separately into `email_context` (full thread) and `metadata.source_metadata` (only for excel-attachment emails — pure-email docs don't even get `source_metadata` saved to `IndexedContent.sheet_metadata`).

**Why:** The pipeline assumes the body has all extractable info. Headers were intentionally minimized to avoid noise — but Subject and recipient often carry the only carrier signal in broker-forwarded emails (e.g., subject `[External]FG March & April Grid Confirmation`, To: `Derryl.George2@futuregenerali.in`).

**How to apply:** When provider extraction looks wrong on an .eml doc, look at the *full* email (S3 file or `email_context` column) — not just `primary_content`. The signal may be in the subject or recipient that Pass 1 never saw.
