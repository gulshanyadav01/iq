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
    # Pre-inject commission_markdown so generated code can read it directly
    safe_locals = {"commission_markdown": commission_markdown}

    # Execution result container (shared between threads)
    exec_result = {"error": None, "done": False}

    def _run():
        try:
            exec(compile(code, "<codegen>", "exec"), safe_globals, safe_locals)  # noqa: S102
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

    result = safe_locals.get("result")
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
