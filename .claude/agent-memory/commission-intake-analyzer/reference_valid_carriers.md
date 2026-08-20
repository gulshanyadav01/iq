---
name: Valid insurance carriers (20 short codes)
description: Canonical 20-carrier vocabulary enforced by cins/schema/carriers.py. Aligned to the user's working-directory folder names as of 2026-05-08.
type: reference
---

The carrier vocabulary is enforced in `backend/cins/schema/carriers.py` (`VALID_CARRIERS`, `CarrierCode` Literal, `CARRIER_FULL_NAMES`, `CARRIER_ALIASES`) and listed in `backend/cins/prompts/pass1_metadata_prompt.md`. As of 2026-05-08 it contains exactly these 20 short codes (matching the user's per-carrier folder layout):

```
Bajaj    HDFC      Liberty     Oriental    Shriram
Chola    ICICI     Magma       Reliance    United
Digit    Iffco     National    RSGI        Universal
FG       Kotak     New India   SBI         Zuno
```

Disambiguation aliases worth knowing:

- `FG` = Future Generali (Future Generali India Insurance)
- `RSGI` = **Royal Sundaram** (Royal Sundaram General Insurance) — NOT Raheja QBE. The original B2 commit incorrectly mapped `RSGI` to Raheja QBE; corrected 2026-05-08.
- `Zuno` = Zuno General (formerly Edelweiss General)

Raheja QBE is NOT in the canonical vocabulary. Per user, any Raheja QBE intake going to `UNKNOWN_PROVIDER` is acceptable — the business does not handle that carrier.

Earlier notes claimed "21" or "25" — both were stale. The folder list is the source of truth and contains exactly 20.

**How to apply:**

- When reconciling docs, group by `provider` and bucket anything outside this 20-code set (or `UNKNOWN_PROVIDER`) for review.
- When fixing provider-extraction misses, look first at whether the carrier is on this list (then add aliases to `CARRIER_ALIASES`). Do **not** add new short codes outside this set without business confirmation — adding broker/aggregator-adjacent vocabulary would re-introduce the broker-misattribution bug B2 was meant to fix.
- When updating the vocabulary, edit in lockstep: `VALID_CARRIERS` tuple + `CarrierCode` Literal + `CARRIER_FULL_NAMES` + `CARRIER_ALIASES` + the prompt grid in `pass1_metadata_prompt.md` + the count guard in `cins/tests/test_carriers.py`. Restart the `celery_commission_intake` worker after deploying changes.
