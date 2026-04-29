"""
Shared prompt fragments used by multiple `.md` prompt templates.

Prompts that include `{codegen_sandbox_rules}` or `{product_taxonomy}` get
these blocks substituted at load time (see `_render_template` in
`cins/processors/core/llm_extract.py`). Centralising them avoids the
"fixed it in pass2 but forgot pass3" failure mode.
"""


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
