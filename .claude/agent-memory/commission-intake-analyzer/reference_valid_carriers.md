---
name: Valid insurance carriers (25 short codes)
description: Canonical short-code list of valid Indian motor-insurance carriers per business; provider extraction must map to one of these
type: reference
---

Per business (user-provided 2026-05-08), the only valid insurance providers in this system are these 25 short codes:

```
Bajaj    HDFC      Liberty     Oriental    Shriram
Chola    ICICI     Magma       Reliance    United
Digit    Iffco     National    RSGI        Universal
FG       Kotak     New India   SBI         Zuno
```

`FG` = Future Generali. As of 2026-05-08 this list is NOT enforced anywhere in the codebase — `CommissionDocument.provider` is an unbounded CharField, no enum/choices/FK, and the Pass 1 prompt is unconstrained ("extract exactly as written"). Treat any provider value not in this list as a data-quality issue.

**How to apply:** When reconciling commission documents, group by `LOWER(provider)` and bucket anything outside this set as "needs review." When proposing fixes for provider-extraction bugs, the fix should anchor on this list (constrain LLM output, post-process via alias map, or both).
