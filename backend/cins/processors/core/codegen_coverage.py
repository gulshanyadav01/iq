"""Deterministic codegen completeness gate.

Pass-2/Pass-3 extraction asks the LLM to generate a *fresh Python parser program*
on every run and keeps whatever it emits. When that program silently drops a whole
table / segment (May run1 `d7f76b67` dropped GCV-3W; June `ada026b9` re-extracted CV
"thin"), the deterministic transformer/overlay downstream has nothing to recover —
some other table's rows are valid, so the pass "succeeds" green and empty.

This module converts the pipeline's *own deterministic scaffolding* into an
acceptance gate:

- At index time we already hold each commission table's raw CSV slice. We count the
  **rate-bearing data rows** the table CONTAINS (host-side, LLM-free) and persist that
  count into ``table_splits`` as ``rate_cell_count``.
- After a codegen attempt, :func:`table_coverage_report` compares the rows codegen
  EMITTED for the sheet against that expected count. A shortfall / dropped table fails
  the attempt so the retry channel re-prompts, and on exhaustion the pass is flagged
  ``requires_review`` instead of shipping a silent-green empty.

Design constraints (from the plan's Eng review):

- **Provider-agnostic.** Uses only the uniform ``[type: commission]`` table grammar and
  host-counted shape — never carrier knowledge.
- **Fail-open.** Absent / legacy shape (docs indexed before the ``rate_cell_count`` key,
  Pass-0C fallback with ``tables: []``) → PASS. A false hard-fail is worse than a miss,
  so every ambiguous case passes.
- **Per-sheet floor is primary; per-table ``source_section`` attribution is advisory.**
  LLM-authored ``source_section`` labels are fuzzy, so per-table attribution can only
  *fail* a table when attribution is demonstrably working for the rest of the sheet.

Rollback: ``CINS_CODEGEN_GATE=false`` disables the gate (always PASS) without a redeploy.
"""

from __future__ import annotations

import csv as _csv
import io as _io
import os
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

# ── Tunables (env-overridable, so the floor can be relaxed without a redeploy) ──


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def gate_enabled() -> bool:
    """False disables the gate entirely (always PASS). Rollback without a redeploy."""
    return _env_flag("CINS_CODEGEN_GATE", True)


def _floor_ratio() -> float:
    """Emitted rows below ``expected * ratio`` count as a shortfall. Conservative
    default: only fires when >~half the expected rate rows are missing."""
    return _env_float("CINS_CODEGEN_COVERAGE_FLOOR_RATIO", 0.5)


def _min_expected() -> int:
    """Sheets/tables with fewer expected rate rows than this are too small to gate on
    — count noise (row merging / fan-out) dominates. Only gate substantive tables."""
    return _env_int("CINS_CODEGEN_COVERAGE_MIN_EXPECTED", 4)


# ── Rate-bearing row counting (host-side, LLM-free) ──────────────────────────
#
# Predicate lifted from ``validators/row_coverage_validator._is_rate`` — a data row
# is "rate-bearing" when at least one cell is a bare numeric rate (optionally %).


def _is_rate(cell: str) -> bool:
    """True when a cell is a bare numeric rate (optionally %-suffixed)."""
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?%?", cell.strip()))


def count_rate_bearing_rows(csv_content: str, header_row_count: int = 0) -> int:
    """Count data rows in a raw CSV table slice that carry at least one rate cell.

    ``header_row_count`` leading rows are treated as the (possibly compound) column
    header and skipped. This is the deterministic "expected" the gate reconciles
    codegen output against — it is intentionally coarse (a floor), not an exact
    rule count, because one grid row can fan out to several rules downstream.
    """
    if not csv_content:
        return 0
    try:
        rows = list(_csv.reader(_io.StringIO(csv_content)))
    except Exception:
        return 0
    # Use the SAME effective header band the markdown build uses. A data row
    # swallowed into the header band is a rate-bearing row codegen can now see,
    # so it must be part of "expected" too — otherwise the gate under-counts by
    # exactly the rows most at risk of being dropped.
    from cins.processors.content.csv_to_markdown import effective_header_row_count

    hrc = effective_header_row_count(rows, header_row_count)
    data_rows = rows[hrc:]
    count = 0
    for row in data_rows:
        if any(_is_rate(c) for c in row):
            count += 1
    return count


def _split_md_cells(line: str) -> List[str]:
    """Split a grid line into cells (pipe, else comma, else 2+ spaces)."""
    if "|" in line:
        parts = line.split("|")
    elif "," in line:
        parts = line.split(",")
    else:
        parts = re.split(r"\s{2,}", line)
    return [p.strip() for p in parts]


def count_rate_bearing_rows_markdown(markdown: str) -> int:
    """Count rate-bearing rows in the pass-3 markdown codegen actually reads.

    Works for ANY commission sheet — split into ``## Table N`` blocks or a single
    grid — because it counts every line that carries at least one numeric rate cell.
    Text headers carry no rate cell so they self-exclude; separator rules are
    skipped. This is the universal per-sheet "expected" floor: table_splits is empty
    for single-grid sheets (e.g. the Chola email attachments), so the index-time
    per-table count alone would leave exactly the client-visible docs ungated.
    """
    if not markdown:
        return 0
    count = 0
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or set(line) <= set("|-: "):  # blank / separator rule
            continue
        cells = _split_md_cells(line)
        if any(_is_rate(c) for c in cells):
            count += 1
    return count


# ── Coverage report ──────────────────────────────────────────────────────────


@dataclass
class TableCoverage:
    table_index: Any
    table_name: str
    expected: int
    attributed: int


@dataclass
class CoverageReport:
    passed: bool
    expected: int = 0
    emitted: int = 0
    gap_message: str = ""
    advisory: List[str] = field(default_factory=list)
    per_table: List[TableCoverage] = field(default_factory=list)
    # Why the gate passed/failed — for logging, never carrier-specific.
    reason: str = ""

    @property
    def failed(self) -> bool:
        return not self.passed


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _name_tokens(table_name: str) -> List[str]:
    """Significant (len>=4) tokens of a table name for fuzzy source_section matching."""
    return [t for t in _norm(table_name).split() if len(t) >= 4]


def _rule_source(rule: Any) -> str:
    """Best-effort source_section text off an emitted rule (pydantic obj or dict)."""
    if rule is None:
        return ""
    src = getattr(rule, "source_section", None)
    if src is None and isinstance(rule, dict):
        src = rule.get("source_section")
    return _norm(src or "")


def _attributes_to(rule_src: str, table: dict) -> bool:
    """Does an emitted rule's source_section point at this table?

    Matches on the explicit ``Table N`` marker (robust) or any significant name
    token (fuzzy). Attribution is only ever used to *confirm* a table was covered —
    a table with zero attributed rules is a drop *candidate*, promoted to a failure
    only when attribution is demonstrably working sheet-wide (see report logic).
    """
    if not rule_src:
        return False
    idx = table.get("table_index")
    if idx is not None and re.search(rf"\btable\s*{re.escape(str(idx))}\b", rule_src):
        return True
    for tok in _name_tokens(table.get("table_name", "")):
        if tok in rule_src:
            return True
    return False


def _commission_tables_with_shape(table_shape: Optional[dict]) -> List[dict]:
    """Commission tables carrying a persisted ``rate_cell_count`` (i.e. new-shape).

    Returns [] when the shape is absent or legacy (no rate_cell_count anywhere) so the
    caller fails open.
    """
    if not isinstance(table_shape, dict):
        return []
    tables = table_shape.get("tables")
    if not isinstance(tables, list) or not tables:
        return []
    out = []
    for t in tables:
        if not isinstance(t, dict):
            continue
        if t.get("table_type", "commission") != "commission":
            continue
        if "rate_cell_count" not in t:
            continue  # legacy table dict — fail open
        out.append(t)
    return out


_TABLE_TYPE_RE = re.compile(r"\[type:\s*([A-Za-z]+)\s*\]")
_NON_RATE_TABLE_TYPES = frozenset({"notes", "master"})


def declares_only_non_rate_tables(markdown: Optional[str]) -> bool:
    """True only when the content POSITIVELY declares no rate-bearing table.

    This is the structural witness that ``count_rate_bearing_rows_markdown`` cannot
    be: that counter is a heuristic over cell text (``_is_rate`` is a strict
    ``fullmatch``), so a real payout grid whose cells read ``22.5 %``, ``Upto 25%``,
    ``25%-30%`` or ``Rs 500`` counts ZERO rate-bearing rows. Anything using the
    counter alone to SUPPRESS a failure signal will therefore accept a genuinely
    dropped grid as an empty sheet.

    The ``[type: ...]`` markers come from the indexer's table tagging, not from cell
    formatting, so they survive whatever the rates look like. A notes/conditions
    sheet tags every table ``notes`` or ``master``; any real grid carries at least
    one ``commission``.

    Fails SAFE in both unknown directions — returns False (i.e. "assume there IS
    rate data") when the content carries no markers at all, or when any marker is
    something other than notes/master. Only an explicit, complete notes/master
    declaration returns True.
    """
    if not markdown:
        # No content is genuinely nothing to extract.
        return True
    found = _TABLE_TYPE_RE.findall(markdown)
    if not found:
        return False
    return all(t.strip().lower() in _NON_RATE_TABLE_TYPES for t in found)


def expected_rate_cells(table_shape: Optional[dict]) -> int:
    """Rate cells the INDEX-TIME shape says this sheet holds.

    Counted from the raw CSV slice at index time, so it is independent of the
    markdown codegen actually read — and the two can disagree (a mangled or
    re-banded markdown conversion drops rows the CSV had). That disagreement is
    the whole point of this helper: a caller deciding "this sheet has nothing to
    extract" must not reach that conclusion from the markdown alone while the
    persisted shape still claims rate cells.

    **0 means "the shape offers no evidence of rate cells", NOT "the sheet is
    empty."** An absent or legacy shape also returns 0, so a caller gating on
    ``== 0`` degrades to whatever other evidence it has (for the Pass-3 empty
    branch, the markdown count) rather than gaining a second witness. That is the
    same fail-open direction every other consumer of ``table_splits`` takes, but
    it does mean this function alone must never be treated as proof of emptiness.
    """
    return sum(
        int(t.get("rate_cell_count") or 0)
        for t in _commission_tables_with_shape(table_shape)
    )


def _markdown_floor_report(markdown: str, emitted: int) -> CoverageReport:
    """Per-sheet floor from the pass-3 markdown (no table_splits needed).

    Used for single-grid sheets where ``table_splits`` is empty. Fails only on a
    confident whole-sheet drop / large shortfall — the same conservative floor the
    per-table path uses, minus the per-table attribution.
    """
    expected = count_rate_bearing_rows_markdown(markdown or "")
    if expected <= 0:
        return CoverageReport(
            passed=True, emitted=emitted, reason="no rate rows in markdown"
        )
    if emitted == 0:
        return CoverageReport(
            passed=False,
            expected=expected,
            emitted=emitted,
            gap_message=(
                f"This sheet's grid contains ~{expected} rate-bearing rows but you "
                f"emitted 0 rules — you dropped the whole grid. Re-read the commission "
                f"grid and emit a rule for every rate-bearing row; do not skip any "
                f"section/segment."
            ),
            reason="whole-sheet drop (0 emitted, markdown floor)",
        )
    if expected >= _min_expected() and emitted < expected * _floor_ratio():
        return CoverageReport(
            passed=False,
            expected=expected,
            emitted=emitted,
            gap_message=(
                f"This sheet's grid contains ~{expected} rate-bearing rows but you "
                f"emitted only {emitted} rules — a whole segment/table is missing. "
                f"Re-read the entire grid top-to-bottom and emit rules for every "
                f"rate-bearing row across all sub-sections."
            ),
            reason=f"markdown shortfall ({emitted} < {expected}*{_floor_ratio()})",
        )
    return CoverageReport(
        passed=True, expected=expected, emitted=emitted, reason="ok (markdown floor)"
    )


def table_coverage_report(
    table_shape: Optional[dict],
    emitted_rules: Sequence[Any],
    markdown: Optional[str] = None,
) -> CoverageReport:
    """Reconcile emitted rules against the host-counted rate-bearing rows for a sheet.

    ``table_shape`` is one sheet's ``table_splits`` entry (``{"tables": [...], ...}``)
    with each commission table carrying ``rate_cell_count``. ``emitted_rules`` is the
    flat list of rules codegen produced for that sheet (pydantic objects or dicts with
    a ``source_section``). ``markdown`` is the pass-3 content codegen read — used as a
    universal per-sheet floor when ``table_shape`` has no split tables (single-grid
    sheets), which is the common case for the client-visible failure docs.

    Fails (``passed=False``) only on a confident whole-sheet or whole-table drop; every
    ambiguous / small / legacy case passes.
    """
    emitted_rules = list(emitted_rules or [])
    emitted = len(emitted_rules)

    if not gate_enabled():
        return CoverageReport(passed=True, emitted=emitted, reason="gate disabled")

    tables = _commission_tables_with_shape(table_shape)
    if not tables:
        # No persisted per-table shape (single-grid sheet / legacy / Pass-0C
        # fallback). Fall back to the markdown per-sheet floor when available,
        # else fail open.
        if markdown:
            return _markdown_floor_report(markdown, emitted)
        return CoverageReport(
            passed=True, emitted=emitted, reason="no shape (fail-open)"
        )

    per_table: List[TableCoverage] = []
    rule_sources = [_rule_source(r) for r in emitted_rules]

    expected_total = 0
    for t in tables:
        exp = int(t.get("rate_cell_count") or 0)
        expected_total += exp
        attributed = sum(1 for s in rule_sources if _attributes_to(s, t))
        per_table.append(
            TableCoverage(
                table_index=t.get("table_index"),
                table_name=t.get("table_name", ""),
                expected=exp,
                attributed=attributed,
            )
        )

    advisory = [
        f"Table {c.table_index} ({c.table_name}): expected ~{c.expected} rate rows, "
        f"attributed {c.attributed} emitted rules"
        for c in per_table
    ]

    # ── Primary gate: per-sheet floor ────────────────────────────────────────
    # Whole-sheet drop — unambiguous silent-green. Always fail.
    if expected_total > 0 and emitted == 0:
        msg = _drop_message(per_table, whole_sheet=True)
        return CoverageReport(
            passed=False,
            expected=expected_total,
            emitted=emitted,
            gap_message=msg,
            advisory=advisory,
            per_table=per_table,
            reason="whole-sheet drop (0 emitted)",
        )

    # Sheet-wide shortfall — emitted rows collapsed far below expected.
    if expected_total >= _min_expected() and emitted < expected_total * _floor_ratio():
        msg = (
            f"This sheet's commission tables contain ~{expected_total} rate-bearing rows "
            f"but you emitted only {emitted} rules — a whole table or segment is missing. "
            f"Re-read every ## Table N [type: commission] block and emit rules for each; "
            f"do not drop any table."
        )
        return CoverageReport(
            passed=False,
            expected=expected_total,
            emitted=emitted,
            gap_message=msg,
            advisory=advisory,
            per_table=per_table,
            reason=f"sheet shortfall ({emitted} < {expected_total}*{_floor_ratio()})",
        )

    # ── Secondary gate: per-table drop, but only when attribution is working ──
    # Guard against fuzzy-label false-fails (Eng E2): only trust a "0 attributed"
    # signal when the majority of emitted rules DID attribute to some table.
    attributed_total = sum(
        1 for s in rule_sources if any(_attributes_to(s, t) for t in tables)
    )
    attribution_working = emitted > 0 and attributed_total >= emitted * 0.5

    if attribution_working:
        dropped = [
            c for c in per_table if c.expected >= _min_expected() and c.attributed == 0
        ]
        if dropped:
            msg = _drop_message(dropped, whole_sheet=False)
            return CoverageReport(
                passed=False,
                expected=expected_total,
                emitted=emitted,
                gap_message=msg,
                advisory=advisory,
                per_table=per_table,
                reason=f"dropped table(s): {[c.table_index for c in dropped]}",
            )

    return CoverageReport(
        passed=True,
        expected=expected_total,
        emitted=emitted,
        advisory=advisory,
        per_table=per_table,
        reason="ok",
    )


def _drop_message(tables: Sequence[TableCoverage], *, whole_sheet: bool) -> str:
    parts = []
    for c in tables:
        parts.append(
            f"Table {c.table_index} ({c.table_name}) contains ~{c.expected} "
            f"rate-bearing rows but you emitted {c.attributed} rules for it"
        )
    body = "; ".join(parts)
    if whole_sheet:
        return (
            f"You emitted 0 rules for this sheet, but it has rate-bearing commission "
            f"tables: {body}. Re-read every ## Table N [type: commission] block and "
            f"emit rules for each — do not skip any table."
        )
    return (
        f"{body}. Re-emit the missing table(s): re-read the corresponding "
        f"## Table N [type: commission] block and emit a rule for each rate-bearing row."
    )
