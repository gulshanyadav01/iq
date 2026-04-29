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
    numbers ('32.5' = 32.5%, '65' = 65%). Detect by magnitude:
      - 0 < |f| <= 1   -> fraction, multiply by 100
      - |f| > 1        -> already a percentage, do not multiply

    Values > 100 are returned as-is so they surface as obvious anomalies
    downstream rather than being silently mangled. Returns '' for empty/
    non-numeric, '0%' for zero.
    \"\"\"
    try:
        f = float(v)
        if f == 0:
            return "0%"
        pct = f * 100 if abs(f) <= 1 else f
        s = "{:.4g}%".format(pct)
        return s
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
    Extract a normalised CC or KW range string from a column header.
    Examples: '150cc' -> '150', '150-350cc' -> '150-350', '> 16 KW' -> '>16',
              '7 KW Bike' -> '7', 'SCOOTER' -> '', 'Hero OEM' -> ''.
    \"\"\"
    h = header.strip().upper()
    for unit in ("CC", "KW"):
        h = h.replace(unit, " ")
    h = h.strip()
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
