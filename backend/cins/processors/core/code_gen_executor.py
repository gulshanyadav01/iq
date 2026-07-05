#!/usr/bin/env python3
"""
Code Generation Executor

Safely executes LLM-generated Python code that produces List[RawCommissionEntry] dicts.
Used as a fallback when JSON extraction exceeds token limits for large documents.

Safety model:
- AST validation blocks imports, exec/eval, dunder access, and other dangerous constructs
- exec() runs in a restricted namespace with whitelisted builtins only
- Threading timeout prevents infinite loops
- Pydantic validation ensures schema compliance of every output dict
"""

import ast
import threading
from typing import List, Tuple

# Helper functions pre-injected into the codegen sandbox.
# The LLM does not need to (and SHOULD NOT) redefine these — they are always
# available. Keeping them here in one place means:
#   - Pass 2 + Pass 3 prompts shrink (no inlined helper source).
#   - When a helper bug is found we fix it once, in code, not in two .md files.
#   - The exact same `to_pct` semantics apply to every codegen run.
_SANDBOX_HELPERS = """
def parse_md_tables(md):
    \"\"\"
    Split a markdown string into ordered (name, table_type, lines) tuples.

    Recognises:
      - Typed tables:   `## Table 1: Foo [type: commission]` -> type from marker
      - Master refs:    `## Master Data Reference: Foo`      -> type "master"
      - Commission:     `## Commission Data Sheet: Foo`      -> type "commission"
                        `## Email Commission Data`           -> type "commission"
      - Other `## ...`  -> type "unknown"

    Any content appearing BEFORE the first heading (provider info, RTO maps,
    global settings) is preserved as a leading entry with name=None,
    type="unknown". Inspect tables[0] for that context.
    \"\"\"
    tables = []
    current_name = None
    current_type = "unknown"
    current_lines = []
    for line in md.splitlines():
        stripped = line.strip()
        is_heading = stripped.startswith("## ")
        if is_heading and "[type:" in stripped:
            if current_name is not None or current_lines:
                tables.append((current_name, current_type, current_lines))
            parts = stripped.split("[type:")
            current_name = parts[0].strip().lstrip("#").strip()
            current_type = parts[1].rstrip("]").strip()
            current_lines = []
        elif is_heading:
            if current_name is not None or current_lines:
                tables.append((current_name, current_type, current_lines))
            current_name = stripped.lstrip("#").strip()
            if "Master Data Reference:" in stripped or "Master Data" in stripped:
                current_type = "master"
            elif "Commission Data Sheet:" in stripped or "Email Commission Data" in stripped:
                current_type = "commission"
            else:
                current_type = "unknown"
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None or current_lines:
        tables.append((current_name, current_type, current_lines))
    return tables

def parse_md_row(line):
    \"\"\"Split a markdown pipe-row into a list of stripped cell strings.\"\"\"
    return [c.strip() for c in line.strip("|").split("|")]

def to_pct(v):
    \"\"\"
    Convert a raw cell value to a percentage string.

    Cells may be stored as fractions ('0.325' = 32.5%) OR as already-percent
    numbers ('32.5' = 32.5%, '65' = 65%) OR with an explicit '%' suffix
    ('30%' = 30%). Detect by magnitude:
      - has '%' suffix -> strip it, treat the remainder as a percentage
      - 0 < |f| <= 1   -> fraction, multiply by 100
      - |f| > 1        -> already a percentage, do not multiply

    Values > 100 are returned as-is so they surface as obvious anomalies
    downstream rather than being silently mangled. Returns '' for empty/
    non-numeric, '0%' for zero.
    \"\"\"
    try:
        s_in = str(v).strip() if v is not None else ""
        had_pct = s_in.endswith("%")
        if had_pct:
            s_in = s_in[:-1].strip()
        f = float(s_in)
        if f == 0:
            return "0%"
        if had_pct:
            pct = f
        else:
            pct = f * 100 if abs(f) <= 1 else f
        return "{:.4g}%".format(pct)
    except (ValueError, TypeError):
        return ""

def extract_notes(tables):
    \"\"\"Concatenate text from every notes/master table into one string.\"\"\"
    parts = []
    for name, ttype, lines in tables:
        if ttype in ("notes", "master"):
            parts.append(" ".join(
                line.strip().strip("|").strip("*").strip()
                for line in lines if line.strip() and "---" not in line
            ))
    return " ".join(parts).strip()

def parse_cc_range(header):
    \"\"\"
    Extract a normalised CC (engine-displacement) range string from a header.
    Examples: '150cc' -> '150', '150-350cc' -> '150-350',
              'SCOOTER' -> '', 'Hero OEM' -> ''.

    kW-rated headers are REJECTED (return '') — electric-vehicle power bands
    ('7 KW', '7 KW Bike', '7 KW - 16 KW', '> 16 KW') are NOT engine CC and
    must not be modelled as a CC range. Mapping kW into CC produced garbage
    ranges like cc=(7,7) that collapsed every EV band into one. Electric
    vehicles carry no CC filter; the body word (Bike/Scooter) is still routed
    to vehicle_sub_class by the caller.

    Reject GVW-shaped headers ('UPTO 3.5T', '2_UPTO_3.5T', '3500 KG',
    'ABOVE 40T') so they fall through to parse_gvw_range — otherwise the
    indiscriminate digit-extraction below mangles tonnage slugs into junk
    CC ranges (e.g. '2_UPTO_3.5T[Electric]' -> '2-3.5' -> cc=(2,4)).
    Symmetric to the CC-rejection guard in parse_gvw_range.
    \"\"\"
    if header is None:
        return ""
    h = header.strip().upper()
    if not h:
        return ""
    # GVW-shape guards: tonnage (digit + T at word boundary), explicit KG,
    # or GVW marker. These belong to parse_gvw_range.
    hu_norm = h.replace("_", " ")
    if "GVW" in hu_norm or "KG" in hu_norm.split() or hu_norm.endswith(" KG"):
        return ""
    # kW-shape guard: any 'KW' token means an EV power band, not CC. Match a
    # bare 'KW' token or a glued form ('7KW', '>16KW') so both '7 KW' and
    # '7KW' are rejected.
    if any(t == "KW" or t.endswith("KW") for t in hu_norm.replace("-", " ").split()):
        return ""
    i = 0
    while i < len(hu_norm):
        if hu_norm[i] == "T":
            j = i - 1
            while j >= 0 and hu_norm[j] == " ":
                j -= 1
            prev_digit = j >= 0 and hu_norm[j].isdigit()
            nxt = hu_norm[i + 1] if i + 1 < len(hu_norm) else ""
            if prev_digit and not nxt.isalpha():
                return ""
        i += 1
    h = h.replace("CC", " ").strip()
    for prefix in (">", ">=", "ABOVE", "GREATER THAN", "MORE THAN"):
        if h.startswith(prefix):
            rest = h[len(prefix):].strip().split()[0] if h[len(prefix):].strip() else ""
            digits = "".join(c for c in rest if c.isdigit() or c == ".")
            if digits:
                return ">" + digits
    parts = h.replace("_", " ").replace("-", " ").split()
    nums = ["".join(c for c in p if c.isdigit() or c == ".") for p in parts if any(c.isdigit() for c in p)]
    if len(nums) >= 2:
        return nums[0] + "-" + nums[1]
    if len(nums) == 1:
        return nums[0]
    return ""

def parse_gvw_range(header):
    \"\"\"
    Detect a Gross Vehicle Weight range label on a commercial-vehicle grid
    header (or product/subclass slug). Returns the verbatim label when the
    header encodes a GVW range, else ''.

    Examples:
        'UPTO 3.5T'      -> 'UPTO 3.5T'
        '3.5T TO 7.5T'   -> '3.5T TO 7.5T'
        '12T TO 16T'     -> '12T TO 16T'
        'ABOVE 40T'      -> 'ABOVE 40T'
        '3500-7500 KG'   -> '3500-7500 KG'
        'GVW < 2000'     -> 'GVW < 2000'
        '2_UPTO_3.5T'    -> '2_UPTO_3.5T'
        'GCCV'           -> ''
        'SCOOTER'        -> ''
        '150cc'          -> ''  (use parse_cc_range for CC headers)

    Pass the verbatim label through to conditions['gvw']; the transformer
    parses the numeric range and converts tonnes to kilograms.
    \"\"\"
    if header is None:
        return ""
    h = str(header).strip()
    if not h:
        return ""
    hu = h.upper().replace("_", " ")
    if not any(c.isdigit() for c in hu):
        return ""
    # Reject CC/KW engine-displacement headers — those belong to parse_cc_range.
    if "CC" in hu.split() or hu.endswith("CC") or "KW" in hu.split() or hu.endswith("KW"):
        return ""
    # Strong indicators: explicit GVW or KG unit.
    if "GVW" in hu or "KG" in hu:
        return h
    # Tonnage scan: a digit immediately followed by 'T' that is not part of
    # another word (e.g. 'TO', 'TRACTOR'). Allow optional whitespace.
    i = 0
    while i < len(hu):
        if hu[i] == "T":
            prev = hu[i - 1] if i > 0 else ""
            # Look back across optional spaces for a digit.
            j = i - 1
            while j >= 0 and hu[j] == " ":
                j -= 1
            prev_digit = j >= 0 and hu[j].isdigit()
            nxt = hu[i + 1] if i + 1 < len(hu) else ""
            if prev_digit and not nxt.isalpha():
                return h
        i += 1
    # Range / qualifier with at least one digit.
    qualifiers = ("UPTO", "UP TO", "ABOVE", "BELOW", "GREATER THAN", "MORE THAN", "LESS THAN", ">", "<")
    if any(q in hu for q in qualifiers):
        return h
    return ""

def parse_nop_range(label):
    \"\"\"
    Extract a number-of-policies range from a row label.

    Examples:
        'NEW(UPTO 30 NOP)'    -> 'UPTO 30'
        'NEW(30-100 NOP)'     -> '30-100'
        'NEW(100-500 NOP)'    -> '100-500'
        'NEW(ABOVE 500 NOP)'  -> 'ABOVE 500'
        'NEW UPTO 30 NOP'     -> 'UPTO 30'
        'NEW'                 -> ''
        'ANNUAL'              -> ''
        ''                    -> ''

    Returns the bare range string (no 'NOP' suffix) or '' when no NOP
    qualifier is present. Pass through unchanged to conditions['policies_count'];
    the transformer parses the numeric range.
    \"\"\"
    if not label:
        return ''
    s = str(label).strip().upper()
    if 'NOP' not in s:
        return ''
    # Isolate the text between the last '(' before NOP and the closing ')',
    # or the slice ending at NOP when parens are absent.
    nop_idx = s.find('NOP')
    open_idx = s.rfind('(', 0, nop_idx)
    if open_idx != -1:
        close_idx = s.find(')', nop_idx)
        if close_idx == -1:
            close_idx = len(s)
        chunk = s[open_idx + 1:close_idx]
    else:
        chunk = s[:nop_idx]
    # Strip the trailing NOP token.
    nop_pos = chunk.rfind('NOP')
    if nop_pos != -1:
        chunk = chunk[:nop_pos]
    chunk = chunk.replace('\\u2013', '-').strip()
    if not chunk:
        return ''
    # When parens are absent the chunk may carry a leading prefix like
    # "NEW " before the actual range. Drop tokens until we hit something
    # that looks like a range start (UPTO / ABOVE / a digit).
    tokens = chunk.split()
    while tokens and tokens[0] not in ('UPTO', 'ABOVE', 'BELOW', 'UP', 'LESS') \\
            and not tokens[0][0].isdigit():
        tokens.pop(0)
    chunk = ' '.join(tokens).strip()
    if not chunk:
        return ''
    # Normalise 'X to Y' to 'X-Y'. Word-bounded match to avoid eating
    # 'UPTO' / 'ABOVE'.
    parts = chunk.split()
    normalised = []
    i = 0
    while i < len(parts):
        token = parts[i]
        if token == 'TO' and 0 < i < len(parts) - 1 and any(c.isdigit() for c in parts[i - 1]) and any(c.isdigit() for c in parts[i + 1]):
            normalised[-1] = normalised[-1] + '-' + parts[i + 1]
            i += 2
            continue
        normalised.append(token)
        i += 1
    chunk = ' '.join(normalised).strip()
    # Collapse spaces around '-' between digits.
    if '-' in chunk and chunk[0].isdigit():
        chunk = chunk.replace(' - ', '-').replace(' -', '-').replace('- ', '-')
    return chunk

def get_entry_type(table_name, header):
    tl = table_name.lower()
    hl = " ".join(header).lower()
    if any(w in tl or w in hl for w in ("rto", "zone", "category", "tier")):
        return "rto_mapping"
    if any(w in tl or w in hl for w in ("declined", "excluded", "restricted")):
        return "declined"
    if any(w in tl or w in hl for w in ("vehicle", "class", "segment")):
        return "vehicle_classification"
    if any(w in tl or w in hl for w in ("state", "region", "territory")):
        return "state_mapping"
    return "general_term"

def is_rto_code(s):
    s = s.strip().upper().replace("-", "")
    return len(s) >= 3 and s[:2].isalpha() and s[2:].isdigit() and len(s[2:]) <= 3

def extract_rto_entries(table_name, header, data_rows, applies_to_hint):
    vehicle_keywords = ("scooter", "bike", "2w", "4w", "moped", "car", "pcv", "gcv", "cv", "motor")
    entries = []
    if not data_rows:
        return entries
    col0_vals = []
    for row in data_rows:
        if row and row[0].strip():
            col0_vals.append(row[0].strip())
    rto_count = sum(1 for s in col0_vals if is_rto_code(s))
    rto_in_col0 = rto_count > len(col0_vals) / 2 if col0_vals else False
    if rto_in_col0:
        # Detect layout: are cells in non-col-0 columns category labels (e.g. "Category 4")
        # or are they presence markers (ticks, Y/N, value-per-column-header)?
        # If any column's header contains a vehicle keyword, treat cell values in that
        # column as category labels and pivot by (column_header, cell_value).
        vehicle_col_indices = []
        other_col_indices = []
        for hi in range(1, len(header)):
            hl = header[hi].lower()
            if any(v in hl for v in vehicle_keywords):
                vehicle_col_indices.append(hi)
            else:
                other_col_indices.append(hi)
        if vehicle_col_indices:
            # Layout: col 0 = RTO code, vehicle columns contain category labels as cell values.
            # Group RTO codes by (vehicle_header, category_label).
            grouped = {}
            for vcol in vehicle_col_indices:
                vehicle_label = header[vcol].strip()
                for row in data_rows:
                    rto_code = row[0].strip() if len(row) > 0 else ""
                    cell = row[vcol].strip() if len(row) > vcol else ""
                    if not rto_code or not is_rto_code(rto_code) or not cell:
                        continue
                    group_key = (vehicle_label, cell)
                    if group_key not in grouped:
                        grouped[group_key] = []
                    grouped[group_key].append(rto_code)
            for (vehicle_label, cat_label), codes in grouped.items():
                entries.append({
                    "entry_type": "rto_mapping",
                    "applies_to": vehicle_label,
                    "key": cat_label,
                    "values": ", ".join(sorted(set(codes))),
                    "notes": "",
                    "source_sheet": table_name,
                    "is_global_rule": True,
                    "priority": None,
                })
        else:
            # Original Layout A: column headers are category labels, cells are presence markers.
            cat_index = []
            col_idx = 1
            while col_idx < len(header):
                cat_label = header[col_idx].strip()
                if cat_label:
                    cat_index.append((col_idx, cat_label))
                col_idx = col_idx + 1
            cat_codes = {}
            for col_idx, cat_label in cat_index:
                codes = []
                for row in data_rows:
                    rto_code = row[0].strip() if len(row) > 0 else ""
                    cell = row[col_idx].strip() if len(row) > col_idx else ""
                    if rto_code and is_rto_code(rto_code) and cell:
                        codes.append(rto_code)
                if codes:
                    cat_codes[cat_label] = codes
            for cat_label in cat_codes:
                codes = cat_codes[cat_label]
                entries.append({
                    "entry_type": "rto_mapping",
                    "applies_to": applies_to_hint,
                    "key": cat_label,
                    "values": ", ".join(sorted(set(codes))),
                    "notes": "",
                    "source_sheet": table_name,
                    "is_global_rule": True,
                    "priority": None,
                })
    else:
        for row in data_rows:
            cat_label = row[0].strip() if row else ""
            if not cat_label:
                continue
            code_parts = []
            for cell in row[1:]:
                for part in cell.split(","):
                    part = part.strip()
                    if part:
                        code_parts.append(part)
            entries.append({
                "entry_type": "rto_mapping",
                "applies_to": applies_to_hint,
                "key": cat_label,
                "values": ", ".join(code_parts),
                "notes": "",
                "source_sheet": table_name,
                "is_global_rule": True,
                "priority": None,
            })
    return entries

"""

# ---------------------------------------------------------------------------
# Host-side helper: detect_qualifying_clauses
#
# Scans the notes/master text blob and returns a structured list of
# {clause_type, text, when?, effect?} entries. Injected into the codegen
# sandbox alongside the markdown helpers so generated code can call it
# without importing `re` (sandbox forbids imports).
#
# Patterns are extended over time as new carrier footnotes are observed.
# Add a new regex + clause builder here; keep the schema stable.
# ---------------------------------------------------------------------------

import re as _re_clause

_RE_APPLIED_ON_PACKAGE = _re_clause.compile(
    r"private\s*car\s*package.*?pay-?out.*?on\s+OD\s*Cover\s*Premium",
    _re_clause.IGNORECASE,
)
_RE_APPLIED_ON_LIABILITY = _re_clause.compile(
    r"private\s*car\s*liability.*?pay-?out.*?on\s+TP\s*/?\s*PA\s*Premium",
    _re_clause.IGNORECASE,
)
# Captures both the percentage and the payout basis. The basis varies by
# product: Private-Car grids word it "OD Pay-out", Commercial-Vehicle grids
# "Net Payout" (Chola). "Pay-?\s*out" tolerates "Payout" / "Pay-out" / "Pay out".
_RE_CPA_DEDUCTION = _re_clause.compile(
    r"CPA\s+(?:is\s+)?not\s+collected.*?individual.*?(\d+(?:\.\d+)?)\s*%.*?(OD|Net)\s*Pay-?\s*out.*?deduct",
    _re_clause.IGNORECASE,
)
_RE_CPA_MANDATORY = _re_clause.compile(
    r"CPA\s+should\s+be\s+collected\s+for\s+individual\s+Name\s+Mandator",
    _re_clause.IGNORECASE,
)
_RE_GRID_ALIAS_EV = _re_clause.compile(
    r"(electric\s*vehicle|EV).*?(LPG|CNG).*?pay-?out\s*grid.*?same\s*as.*?petrol",
    _re_clause.IGNORECASE,
)


def detect_qualifying_clauses(notes_text, provider=""):
    """Scan free-text notes for known qualifying-clause shapes.

    Returns a list of {clause_type, text, when?, effect?} dicts the
    downstream pipeline can ingest programmatically. Empty list when the
    input is empty or no clause matches.

    Stable per (notes_text, provider) — same input always returns same list.

    Recognised clause shapes (extend by adding to the patterns above):
      - applied_on_override     : "Private Car Package pay-out is on OD Cover Premium"
      - applied_on_override     : "Private Car Liability pay-out is on TP/PA Premium"
      - conditional_deduction   : "if CPA not collected for individual name, X% of OD Pay-out deducted"
      - mandatory_requirement   : "CPA should be collected for individual Name Mandatorily"
      - grid_alias              : "Private Car Electric Vehicle or LPG/CNG pay-out grid is same as Private Car Petrol grid"
    """
    if not notes_text:
        return []
    text = " ".join(str(notes_text).split())  # collapse whitespace
    clauses = []

    if _RE_APPLIED_ON_PACKAGE.search(text):
        clauses.append({
            "clause_type": "applied_on_override",
            "text": "Private Car Package pay-out is on OD Cover Premium",
            "when": {"product_name": "Private Car", "coverage_type_text": "comprehensive"},
            "effect": {"applied_on": "OD Cover Premium"},
        })

    if _RE_APPLIED_ON_LIABILITY.search(text):
        clauses.append({
            "clause_type": "applied_on_override",
            "text": "Private Car Liability pay-out is on TP/PA Premium",
            "when": {"product_name": "Private Car", "coverage_type_text": "third party"},
            "effect": {"applied_on": "TP/PA Premium"},
        })

    m = _RE_CPA_DEDUCTION.search(text)
    if m:
        pct_str = m.group(1)
        try:
            pct = float(pct_str)
        except (ValueError, TypeError):
            pct = None
        if pct is not None:
            # Reflect the actual payout basis the footnote names. cpa_split
            # applies the deduction to the rule's own payout column (OD or Net)
            # regardless, but carrying the right basis keeps the clause honest
            # and lets it attach to CV (Net) rules, not just Private Car (OD).
            basis = "Net Payout" if m.group(2).upper() == "NET" else "OD Pay-out"
            clauses.append({
                "clause_type": "conditional_deduction",
                "text": (
                    "if CPA is not collected for individual name then "
                    + pct_str
                    + "% of " + basis + " will be deducted"
                ),
                "when": {
                    "coverage_type_text": ["comprehensive", "saod", "third party"],
                    "school_bus_type": "on-individual-name",
                    "cpa": "without-cpa",
                },
                "effect": {
                    "deduction": {
                        "value": pct,
                        "value_type": "percentage",
                        "applied_on": basis,
                    }
                },
            })

    if _RE_CPA_MANDATORY.search(text):
        clauses.append({
            "clause_type": "mandatory_requirement",
            "text": "CPA should be collected for individual Name Mandatorily",
            "when": {
                "product_name": "Private Car",
                "coverage_type_text": ["comprehensive", "saod", "third party"],
                "school_bus_type": "on-individual-name",
            },
            "effect": {"required": ["cpa"]},
        })

    if _RE_GRID_ALIAS_EV.search(text):
        clauses.append({
            "clause_type": "grid_alias",
            "text": "Private Car Electric Vehicle or LPG/CNG pay-out grid is same as Private Car Petrol grid",
            "when": {"product_name": "Private Car", "fuel_type": ["electric", "lpg", "cng"]},
            "effect": {"inherit_from": {"fuel_type": "petrol"}},
        })

    return clauses


class CodeGenExecutionError(Exception):
    """Raised when the generated code fails to execute."""
    pass


class CodeGenValidationError(Exception):
    """Raised when the generated code contains dangerous constructs."""
    pass


# Builtins the generated code is allowed to use
SAFE_BUILTIN_NAMES = {
    "range", "len", "list", "dict", "str", "int", "float", "bool",
    "tuple", "set", "enumerate", "zip", "map", "filter", "sorted",
    "min", "max", "sum", "abs", "round", "any", "all", "isinstance", "print",
    "True", "False", "None",
    # Exception classes needed for try/except in generated helpers (e.g. to_pct)
    "ValueError", "TypeError", "IndexError", "KeyError", "AttributeError",
    "StopIteration", "Exception",
}

# AST node types that are always forbidden
_FORBIDDEN_NODE_TYPES = (ast.Import, ast.ImportFrom)

# Function/attribute names that are forbidden
_FORBIDDEN_NAMES = {
    "exec", "eval", "compile", "open", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "breakpoint", "input", "memoryview", "bytearray", "bytes",
    "classmethod", "staticmethod", "property", "super",
}


class _ASTValidator(ast.NodeVisitor):
    """Walks the AST and raises CodeGenValidationError on forbidden constructs."""

    def visit_Import(self, node):
        raise CodeGenValidationError(
            f"Import statements are not allowed (line {node.lineno})"
        )

    def visit_ImportFrom(self, node):
        raise CodeGenValidationError(
            f"Import statements are not allowed (line {node.lineno})"
        )

    def visit_Call(self, node):
        # Block calls to forbidden function names
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr

        if name and name in _FORBIDDEN_NAMES:
            raise CodeGenValidationError(
                f"Call to '{name}' is not allowed (line {node.lineno})"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # Block access to dunder attributes
        if node.attr.startswith("__") and node.attr.endswith("__"):
            raise CodeGenValidationError(
                f"Access to dunder attribute '{node.attr}' is not allowed (line {node.lineno})"
            )
        self.generic_visit(node)

    def visit_Name(self, node):
        # Block direct references to forbidden names (e.g., globals used as variable)
        if node.id in _FORBIDDEN_NAMES:
            raise CodeGenValidationError(
                f"Reference to '{node.id}' is not allowed (line {node.lineno})"
            )
        self.generic_visit(node)


def validate_code_ast(code: str) -> None:
    """
    Parse and walk the AST of generated code, raising CodeGenValidationError
    if any dangerous constructs are found.

    Args:
        code: Python source code string from LLM

    Raises:
        CodeGenValidationError: If forbidden constructs are detected
        CodeGenExecutionError: If the code cannot be parsed at all
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise CodeGenExecutionError(f"Generated code has syntax error: {e}")

    validator = _ASTValidator()
    validator.visit(tree)


def execute_entry_code(
    code: str,
    commission_markdown: str = "",
    timeout_seconds: int = 10,
) -> List[dict]:
    """
    Execute LLM-generated Python code in a restricted namespace and return
    the value of the `result` variable as a list of dicts.

    The code must set a variable named `result` to a list of dicts.
    The variable `commission_markdown` is pre-injected so the generated code
    can read the commission content at runtime instead of hardcoding it.

    Args:
        code: Python source code (already AST-validated)
        commission_markdown: The full commission content string to make available
                             to the generated code as the `commission_markdown` variable.
        timeout_seconds: Maximum execution time before aborting

    Returns:
        List of dicts from the `result` variable

    Raises:
        CodeGenExecutionError: If execution fails, times out, or result is invalid
    """
    # Build restricted builtins
    import builtins as _builtins_module
    safe_builtins = {}
    for name in SAFE_BUILTIN_NAMES:
        if hasattr(_builtins_module, name):
            safe_builtins[name] = getattr(_builtins_module, name)

    safe_globals = {
        "__builtins__": safe_builtins,
        "__name__": "__code_gen__",
    }
    # Pre-inject helper functions and commission_markdown into a single namespace.
    # We pass ONLY globals (no separate locals) to exec so that top-level
    # assignments and comprehensions resolve names against the same dict —
    # otherwise comprehensions can't see variables assigned at the top level.
    safe_globals["commission_markdown"] = commission_markdown
    exec(compile(_SANDBOX_HELPERS, "<helpers>", "exec"), safe_globals)  # noqa: S102

    # Inject host-side helpers that need imports the sandbox forbids (e.g.
    # `detect_qualifying_clauses` uses `re`). The bound function carries its
    # own closure to the compiled patterns, so codegen can call it without
    # writing `import re`.
    safe_globals["detect_qualifying_clauses"] = detect_qualifying_clauses

    # Execution result container (shared between threads)
    exec_result = {"error": None, "done": False}

    def _run():
        try:
            exec(compile(code, "<codegen>", "exec"), safe_globals)  # noqa: S102
            exec_result["done"] = True
        except Exception as exc:
            exec_result["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise CodeGenExecutionError(
            f"Generated code execution timed out after {timeout_seconds}s"
        )

    if exec_result["error"] is not None:
        raise CodeGenExecutionError(
            f"Generated code raised an error during execution: {exec_result['error']}"
        )

    if not exec_result["done"]:
        raise CodeGenExecutionError("Generated code did not complete successfully")

    result = safe_globals.get("result")
    if result is None:
        raise CodeGenExecutionError(
            "Generated code did not set a 'result' variable"
        )

    if not isinstance(result, list):
        raise CodeGenExecutionError(
            f"Generated code 'result' must be a list, got {type(result).__name__}"
        )

    return result


def validate_entries(raw_dicts: List[dict]) -> Tuple[list, List[str]]:
    """
    Validate each dict in raw_dicts against the RawCommissionEntry Pydantic model.

    Args:
        raw_dicts: List of dicts produced by the generated code

    Returns:
        Tuple of (valid_entries, error_notes) where:
        - valid_entries: List of validated RawCommissionEntry objects
        - error_notes: List of validation error descriptions (for ambiguities)
    """
    from cins.schema.raw_extraction import RawCommissionEntry  # noqa: PLC0415

    valid_entries = []
    error_notes = []

    for i, raw_dict in enumerate(raw_dicts):
        if not isinstance(raw_dict, dict):
            error_notes.append(
                f"Entry {i}: expected dict, got {type(raw_dict).__name__} — skipped"
            )
            continue
        try:
            entry = RawCommissionEntry.model_validate(raw_dict)
            valid_entries.append(entry)
        except Exception as e:
            product = raw_dict.get("product_name", "unknown")
            rule = raw_dict.get("rule_text", "")[:80]
            error_notes.append(
                f"Entry {i} ({product!r}, {rule!r}): validation failed — {e}"
            )

    return valid_entries, error_notes
