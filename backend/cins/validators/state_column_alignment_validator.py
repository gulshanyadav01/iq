"""
State-Column Alignment Validator

Catches a *column-misalignment* class of bug the RowCoverageValidator and the
CoverageIntegrityValidator cannot see: a Private Car grid row can trace to an
emitted rule AND carry the right coverage, yet the rule's RATE was read from a
NEIGHBOURING state's column. The motivating incident (Chola "PO Grid-Apr 26"):
14 of 21 state columns were read one-or-more slots off — jharkhand's values came
from the gujarat column, chattisgarh's from jharkhand's, etc. — because a
generated parser filtered blank spacer headers before indexing, so every column
after a spacer drifted. Rules were correctly labelled, so nothing else fired.

**Assertion is MEMBERSHIP, not fingerprint.** Every rate emitted for state S must
appear *somewhere in S's own source column*. Fingerprint best-match scoring fails
badly when neighbouring states share rates — and neighbours are exactly where the
drift is — producing confident wrong answers. Membership under-reports and never
over-reports: if AP and TN both pay 32.5% and the parser read TN's column for AP,
this simply does not fire. It also needs no row identification, so CC-band
relabelling, dedupe bumps, and the NCB tier are all irrelevant.

Gate: ``missing = emitted[S] - own_values[S]``; silent if empty; **HIGH** when
exactly one other column X satisfies ``missing ⊆ values[X]`` and there are >=3
distinct missing rates (the signature of an index shift — three-plus distinct
rates all absent from S's column and all present in exactly one other column
essentially cannot occur by chance); **MEDIUM** otherwise.

Severity HIGH is loud but not fatal: like the other coverage validators, issues
merge into the schema result's ``errors`` without recomputing ``is_valid`` and
nothing downstream branches on it — so there is no extraction-failure downside.
Deliberately conservative and fail-open: partial source-trace coverage only
reduces sensitivity, which is acceptable for a membership test.
"""

import json
import re
from typing import Dict, List, Optional, Set

from cins.processors.core.raw_to_cics_transformer import RawToCICSTransformer
from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

# A state x coverage header cell: "AP/TS > ACT", "JH > PACK", "UP- East > ACT".
# The state segment is everything before the last ">"; the coverage token is one
# of the known column suffixes. Anchored so an ordinary "note > see below" cell
# is not mistaken for a header.
_STATE_COVERAGE_RE = re.compile(
    r"^(?P<state>.+?)\s*>\s*(?:ACT|PACK|SOD|OD|TP)$", re.IGNORECASE
)

# Two normalized rates are equal within this tolerance (mirrors to_pct rounding).
_RATE_TOL = 0.005


def _split_row(line: str) -> List[str]:
    """Split a markdown pipe-row into stripped cells, mirroring
    code_gen_executor.parse_md_row EXACTLY (strip outer pipes, then split on
    '|'). NOT row_coverage_validator._split_cells — that one keeps the outer
    empties, so its index is the codegen's ``i-1`` and a mismatched index would
    name the wrong column in the error message. The whole point of this
    validator is that the reported index is the one the codegen author acts on.
    """
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _norm_pct(raw: str) -> Optional[float]:
    """Normalize a source cell to a percentage magnitude, replicating to_pct's
    rule: explicit '%' -> as-is; 0 < |f| <= 1 -> fraction (x100); |f| > 1 ->
    already a percentage. Returns None for blank/non-numeric, 0.0 for zero."""
    s = str(raw).strip()
    had_pct = s.endswith("%")
    if had_pct:
        s = s[:-1].strip()
    try:
        f = float(s)
    except (ValueError, TypeError):
        return None
    if f == 0:
        return 0.0
    if had_pct:
        return f
    return f * 100 if abs(f) <= 1 else f


def _contains(val: float, values: Set[float]) -> bool:
    return any(abs(val - v) < _RATE_TOL for v in values)


class StateColumnAlignmentValidator:
    """Assert every emitted rate for state S appears in S's own source column."""

    def __init__(self):
        # Reused only for its state-name expander; warnings it appends are
        # discarded (this instance never feeds the pipeline).
        self._expander = RawToCICSTransformer()

    def validate(
        self, cics_path: str, sheet_contents: Optional[Dict[str, str]]
    ) -> ValidationResult:
        issues: List[ValidationIssue] = []
        try:
            with open(cics_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            return self._result(
                [
                    ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"State-column alignment skipped: cannot read CICS ({e})",
                        field_path="root",
                        category="State Column Alignment",
                    )
                ]
            )

        sheets = sheet_contents or {}
        eligible = self._eligible_rules(doc)
        sections = self._rule_sections(doc)

        # Fail open: if fewer than half the eligible rules resolve to a section
        # that appears as a heading in some provided sheet, source-trace coverage
        # is too thin to scope reliably — emit INFO and check nothing.
        all_titles: Set[str] = set()
        for content in sheets.values():
            all_titles |= self._sheet_titles(content or "")
        if eligible:
            resolved = sum(
                1
                for r in eligible
                if self._section_matches(sections.get(r["rule_id"]), all_titles)
            )
            if resolved * 2 < len(eligible):
                return self._result(
                    [
                        ValidationIssue(
                            severity=Severity.INFO,
                            message=(
                                f"State-column alignment: only {resolved}/"
                                f"{len(eligible)} rules resolve to a sheet section "
                                f"— skipping (fail open)."
                            ),
                            field_path="source_traces",
                            category="State Column Alignment",
                        )
                    ]
                )

        for sheet_name, content in sheets.items():
            if not content:
                continue
            own = self._state_column_values(content)
            if not own:
                continue  # no state x coverage grid on this sheet
            titles = self._sheet_titles(content)
            emitted = self._emitted_by_state(eligible, sections, titles)
            for state, rates in emitted.items():
                if state not in own:
                    continue  # no column to check against -> under-report
                missing = {v for v in rates if not _contains(v, own[state])}
                if not missing:
                    continue
                foreign = [
                    other
                    for other, ovals in own.items()
                    if other != state and all(_contains(m, ovals) for m in missing)
                ]
                if len(missing) >= 3 and len(foreign) == 1:
                    severity = Severity.HIGH
                    detail = (
                        f"all present in exactly one other column ('{foreign[0]}') "
                        f"— index shift signature"
                    )
                else:
                    severity = Severity.MEDIUM
                    detail = f"present in {len(foreign)} other column(s)"
                sample = ", ".join(f"{v:g}%" for v in sorted(missing)[:5])
                issues.append(
                    ValidationIssue(
                        severity=severity,
                        message=(
                            f"State '{state}' on sheet '{sheet_name}' emits "
                            f"{len(missing)} rate(s) absent from its own source "
                            f"column [{sample}]; {detail}. Likely a column-index "
                            f"misalignment in the generated parser."
                        ),
                        field_path="products[].commission_rules[].conditions.state",
                        category="State Column Alignment",
                        suggestion=(
                            "Enumerate header cells by LITERAL parse_md_row index "
                            "(do not filter/compact blank spacer headers before "
                            "indexing); take the state segment before the last '>' "
                            "verbatim."
                        ),
                    )
                )

        return self._result(issues)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _result(issues: List[ValidationIssue]) -> ValidationResult:
        errors = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.HIGH)]
        warnings = [i for i in issues if i.severity == Severity.MEDIUM]
        info = [i for i in issues if i.severity in (Severity.LOW, Severity.INFO)]
        return ValidationResult(
            is_valid=all(i.severity != Severity.CRITICAL for i in issues),
            errors=errors,
            warnings=warnings,
            info=info,
        )

    def _eligible_rules(self, doc: Dict) -> List[Dict]:
        """Rules that carry a source rate: percentage, non-zero, and NOT
        without-cpa (CPA-split subtracts the deduction, so those rates no longer
        match the source cell — every CPA-split doc would otherwise wall out).

        Scoped to Private Car. The membership assertion ("every emitted rate for
        state S appears in S's own source column") only holds where the rate is
        read verbatim from the cell — true for the PC state x coverage grid.
        Commercial-vehicle grids run rates through GVW dedupe bumps, make/model
        bucket bifurcation and sub-segmentation, so the emitted CV rate no longer
        equals the raw cell and membership false-positives (observed as MEDIUM
        "present in 0 other columns" on the CV sheets in the archive sweep).
        Defect 1 is a Private Car column-index bug; keep the check where it is
        sound."""
        out = []
        for product in doc.get("products", []):
            if not self._is_private_car(product):
                continue
            for rule in product.get("commission_rules", []):
                outcome = rule.get("outcome") or {}
                if outcome.get("commission_type") != "percentage":
                    continue
                pv = outcome.get("percentage_value")
                if pv is None or float(pv) == 0.0:
                    continue
                cond = rule.get("conditions") or {}
                cpa = cond.get("cpa")
                cpa_vals = cpa if isinstance(cpa, (list, tuple)) else [cpa]
                if any(str(c).lower() == "without-cpa" for c in cpa_vals if c):
                    continue
                out.append(rule)
        return out

    @staticmethod
    def _is_private_car(product: Dict) -> bool:
        """True for the Private Car product (where rates are read verbatim from
        the state x coverage grid). Matches on product_name / code / sub_category
        and excludes Taxi and the commercial-vehicle segments."""
        blob = " ".join(
            str(product.get(k) or "")
            for k in ("product_name", "product_code", "sub_category")
        ).lower()
        if "taxi" in blob or "commercial" in blob:
            return False
        return "private car" in blob or "private_car" in blob

    @staticmethod
    def _rule_sections(doc: Dict) -> Dict[str, str]:
        """rule_id -> source_traces section string (best available)."""
        sections: Dict[str, str] = {}
        for trace in doc.get("source_traces") or []:
            if trace.get("entity_type") != "rule":
                continue
            loc = trace.get("source_location") or {}
            section = loc.get("section")
            rid = trace.get("entity_id")
            if rid and section and rid not in sections:
                sections[rid] = section
        return sections

    @staticmethod
    def _sheet_titles(content: str) -> Set[str]:
        """Lower-cased '## <title>' headings present in a sheet's markdown."""
        titles: Set[str] = set()
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("## "):
                titles.add(s[3:].strip().lower())
        return titles

    @staticmethod
    def _section_matches(section: Optional[str], titles: Set[str]) -> bool:
        """Loose prefix match between a rule's section and a sheet's headings —
        mirrors the loose heuristic _entry_matches_rule uses for traces."""
        if not section or not titles:
            return False
        sec = section.strip().lower()
        return any(sec in t or t in sec for t in titles)

    def _emitted_by_state(
        self,
        eligible: List[Dict],
        sections: Dict[str, str],
        titles: Set[str],
    ) -> Dict[str, Set[float]]:
        """Percentage rates emitted per state slug for rules scoped to this
        sheet's sections."""
        out: Dict[str, Set[float]] = {}
        for rule in eligible:
            if not self._section_matches(sections.get(rule["rule_id"]), titles):
                continue
            cond = rule.get("conditions") or {}
            states = cond.get("state")
            if not states:
                continue
            pv = float(rule["outcome"]["percentage_value"])
            for slug in states:
                out.setdefault(str(slug), set()).add(pv)
        return out

    def _state_column_values(self, content: str) -> Dict[str, Set[float]]:
        """Map each state slug to the set of non-zero rates present anywhere in
        its own source column(s). Column indices are LITERAL parse_md_row split
        indices; a new state x coverage header row rebuilds the column map."""
        own: Dict[str, Set[float]] = {}
        col_to_slugs: Dict[int, List[str]] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line.startswith("|"):
                continue
            if set(line) <= set("|-: "):
                continue  # separator row
            cells = _split_row(line)
            header_cols = {
                i: m.group("state").strip()
                for i, c in enumerate(cells)
                if (m := _STATE_COVERAGE_RE.match(c))
            }
            if len(header_cols) >= 3:
                # A state x coverage header — rebuild the column map. Expand each
                # state segment verbatim to canonical slug(s) ("AP/TS" -> both;
                # "UP- East" -> uttar-pradesh).
                col_to_slugs = {}
                for i, state_text in header_cols.items():
                    slugs = self._expander._expand_state_names(state_text, None)
                    if slugs:
                        col_to_slugs[i] = slugs
                continue
            if not col_to_slugs:
                continue
            for i, slugs in col_to_slugs.items():
                if i >= len(cells):
                    continue
                pct = _norm_pct(cells[i])
                if pct is None or pct == 0.0:
                    continue
                for slug in slugs:
                    own.setdefault(slug, set()).add(pct)
        return own
