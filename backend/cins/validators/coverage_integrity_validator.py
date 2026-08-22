"""
Coverage-Integrity Validator

Catches a *coverage-label* class of bug that the RowCoverageValidator cannot see:
a grid row can trace to an emitted rule (so row-coverage passes) yet the rule
carries the WRONG coverage/policy_type — the whole column is silently misfiled.

The motivating incident (Chola doc dbe5b4ea, "July pvt car"): the Private Car
Package grid pairs ``<state> > ACT | <state> > PACK`` sub-columns. ACT is the
Third-Party payout; PACK is Comprehensive. The Pass-3 codegen read every ACT
cell but tagged the whole grid ``comprehensive``, so 24 non-blank ACT cells
landed in the comprehensive bucket and ``third-party`` came out at 0. Every row
produced a rule, so RowCoverageValidator (row -> >=1 rule) stayed silent.

One deterministic check, run against the source grid markdown + the emitted
CICS doc:

  A. **ACT column orphaned** — a source grid carries >=1 non-blank ACT
     (third-party) column cell, but the document emitted ZERO ``third-party``
     rules. Conservative: fires only on the total-absence case (a partial-
     adherence count is not asserted, because CPA-split doubling and state-group
     fan-out make an exact expected count unreliable at the validator layer).
     Severity HIGH.

There used to be a check B — "third-party rule wrongly CPA-split", a HIGH on any
TP rule carrying a ``with-cpa`` / ``without-cpa`` condition — on the reasoning
that TP/PA-only rules are never CPA-deducted. That reasoning was wrong (the
Chola footnote deducts the broker's payout, not the statutory premium), and
since pipeline version 1.8.0 ``cpa_split.materialize_cpa_split`` splits and
deducts third-party rules deliberately. The check was REMOVED rather than
downgraded: it now describes correct output, so it would fire on every affected
document, and at HIGH it would additionally block ``cics_cache.should_cache``
from pinning them — making every Chola document with the CPA footnote
permanently un-cacheable. A finding that is always wrong is worse than no
finding.

Like RowCoverageValidator, this takes the source grid markdown (per-sheet
contents) plus the CICS JSON path, and its issues are merged into the pipeline's
schema result. Deliberately conservative — a false HIGH is worse than a miss.
"""

import json
import re
from typing import Dict, List, Optional

from cins.validators.row_coverage_validator import _is_rate, _split_cells
from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

# A header cell that names a Third-Party sub-column: "AP/TS > ACT", "ACT",
# "TN>ACT". The token must be a standalone ACT (not a substring of a word).
_ACT_HEADER_RE = re.compile(r"(?:^|>)\s*ACT\s*$", re.IGNORECASE)


def _nonzero_rate(cell: str) -> bool:
    """True when a cell is a real, non-zero rate (blank / 0 / 0% -> False)."""
    c = cell.strip()
    if not _is_rate(c):
        return False
    try:
        return float(c.rstrip("%")) != 0.0
    except ValueError:
        return False


class CoverageIntegrityValidator:
    """Assert coverage-labelled columns (ACT/third-party) are not silently misfiled."""

    def validate(
        self, cics_path: str, sheet_contents: Optional[Dict[str, str]]
    ) -> ValidationResult:
        issues: List[ValidationIssue] = []
        try:
            with open(cics_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            return ValidationResult(
                is_valid=True,
                errors=[],
                warnings=[
                    ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"Coverage-integrity skipped: cannot read CICS ({e})",
                        field_path="root",
                        category="Coverage Integrity",
                    )
                ],
                info=[],
            )

        third_party_rules = self._third_party_rules(doc)
        third_party_count = len(third_party_rules)

        # Check A — orphaned ACT columns.
        act_cells = self._count_act_cells(sheet_contents or {})
        total_act = sum(act_cells.values())
        if total_act > 0 and third_party_count == 0:
            sheets = ", ".join(f"'{name}' ({n})" for name, n in act_cells.items() if n)
            issues.append(
                ValidationIssue(
                    severity=Severity.HIGH,
                    message=(
                        f"Source grid has {total_act} non-blank ACT (third-party) "
                        f"column cell(s) [{sheets}] but the document emitted 0 "
                        f"third-party rules — the ACT column was misfiled "
                        f"(likely tagged comprehensive)."
                    ),
                    field_path="products[].commission_rules",
                    category="Coverage Integrity",
                    suggestion=(
                        "Emit coverage_type_text='ACT' per ACT column cell so the "
                        "transformer maps it to policy_type=third-party."
                    ),
                )
            )

        errors = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.HIGH)]
        warnings = [i for i in issues if i.severity == Severity.MEDIUM]
        info = [i for i in issues if i.severity in (Severity.LOW, Severity.INFO)]
        return ValidationResult(
            is_valid=len([i for i in issues if i.severity == Severity.CRITICAL]) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )

    @staticmethod
    def _third_party_rules(doc: Dict) -> List:
        """rule_id for every rule whose policy_type includes third-party.

        Check A only needs the count. This used to also carry each rule's `cpa`
        value for the removed check B — see the module docstring.
        """
        out = []
        for product in doc.get("products", []):
            for rule in product.get("commission_rules", []):
                cond = rule.get("conditions") or {}
                pt = cond.get("policy_type")
                pts = pt if isinstance(pt, (list, tuple)) else [pt]
                if any(str(p).lower() == "third-party" for p in pts if p is not None):
                    out.append(rule.get("rule_id"))
        return out

    @staticmethod
    def _count_act_cells(sheet_contents: Dict[str, str]) -> Dict[str, int]:
        """Per-sheet count of non-blank, non-zero cells under ACT header columns."""
        counts: Dict[str, int] = {}
        for sheet_name, content in sheet_contents.items():
            if not content:
                continue
            act_cols: set = set()
            n = 0
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or set(line) <= set("|-: "):
                    continue
                cells = _split_cells(line)
                header_cols = {
                    i for i, c in enumerate(cells) if _ACT_HEADER_RE.search(c)
                }
                if header_cols:
                    # A header row that declares ACT sub-columns. Remember the
                    # indices; header rows themselves carry no rates to count.
                    act_cols |= header_cols
                    continue
                if not act_cols:
                    continue
                for i in act_cols:
                    if i < len(cells) and _nonzero_rate(cells[i]):
                        n += 1
            if n:
                counts[sheet_name] = n
        return counts
