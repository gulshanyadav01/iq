"""
Shared prompt fragments used by multiple `.md` prompt templates.

Prompts that include `{codegen_sandbox_rules}` or `{product_taxonomy}` get
these blocks substituted at load time (see `_render_template` in
`cins/processors/core/llm_extract.py`). Centralising them avoids the
"fixed it in pass2 but forgot pass3" failure mode.
"""

# The vocabulary of keys an LLM may emit in a raw entry's ``conditions`` dict.
#
# Single source of truth for BOTH RawCommissionEntry.conditions
# (schema/raw_extraction.py) and RuleEntry.conditions
# (schema/extraction_passes.py). These two field descriptions were previously
# duplicated verbatim and kept in sync by hand — the same manual-multi-site
# pattern that lets changes half-apply silently elsewhere in this app. They are
# pinned equal by a test; interpolate this constant, never restate it.
#
# ``ncb`` takes the VERBATIM band label from the grid ("NCB GT OR Equal to 25%",
# "NCB 25%-45%", "NCB UPTO 20%"); the transformer parses it into a 0-100
# percentage band. Legacy "Yes"/"No" values are still accepted.
RAW_CONDITION_KEYS_BLOCK = (
    "Key-value pairs for any grid dimension conditions that have no dedicated field. "
    "Use for: fuel_type, ncb, cpa, zero_dep, vehicle_sub_class, vehicle_carrier, "
    "number_of_wheels, school_bus_type, gvw, cc, seating_capacity, vehicle_age, "
    "vehicle_make, vehicle_model, od_term, tp_term, deal_parameter, rto, etc. "
    "Keys should be snake_case. Values should be the exact label from the grid header. "
    "For ncb, copy the band label verbatim (e.g. 'NCB GT OR Equal to 25%', "
    "'NCB 25%-45%', 'NCB UPTO 20%') — it is parsed into a 0-100 percentage band; "
    "legacy 'Yes'/'No' is still accepted. "
    "Do NOT put here: state (use state_text), booking mode (use channel_text), "
    "policy type (use coverage_type_text), business type (use customer_type_text). "
    'Examples: {"fuel_type": "Non Petrol", "ncb": "NCB GT OR Equal to 25%", '
    '"vehicle_sub_class": "Private Car"}'
)


# Mirrors the exact rules enforced by code_gen_executor._ASTValidator and
# SAFE_BUILTIN_NAMES. If the executor changes, edit this string in lockstep.
CODEGEN_SANDBOX_RULES = """## CODING RULES

Allowed builtins: `range`, `len`, `list`, `dict`, `str`, `int`, `float`, `bool`, `tuple`,
`set`, `enumerate`, `zip`, `sorted`, `min`, `max`, `sum`, `abs`, `round`, `any`, `all`,
`isinstance`, `print`.

String methods are allowed on string instances (`.split()`, `.strip()`, `.startswith()`, etc.)

FORBIDDEN:

- `import`, `from ... import`
- `exec`, `eval`, `compile`, `open`, `__import__`
- `getattr`, `setattr`, `globals`, `locals`, `vars`
- Any attribute starting and ending with `__`
"""


# Canonical product names. Used to disambiguate when a sheet refers to a
# product by abbreviation, code, or trade-name. Update once here when the
# product taxonomy changes; both pass3 prompts pick it up automatically.
PRODUCT_TAXONOMY = """### product_name

Motor products — use exactly one of:

- `"Private Car"` — car, PVT CAR, 4W, four-wheeler, taxi, pvt
- `"Two Wheeler"` — TW, 2W, bike, scooter, motorcycle, moped
- `"Commercial Vehicle"` — GCV, GCCV, PCV, PCCV, MSV, bus, tractor, misc D, CV, agri tractor

Non-motor: use name as written in document.
Set `product_name` as a **string constant** in your code, not a variable read from a column header.
Vehicle makes (Hero, TVS, Honda, Bajaj, etc.) are NOT separate products.
"""
