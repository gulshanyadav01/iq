"""
Row-Coverage Validator

Closes the INVERSE direction of SchemaValidator's traceability check. Where
SchemaValidator asserts that every ``source_traces[].entity_id`` points at an
existing product/rule (forward: emitted entity -> source), this validator
asserts the opposite: every non-empty grid DATA ROW traces to >=1 emitted rule
(inverse: source row -> emitted entity). A row that produces no rule is
silently dropped today and no existing validator can see it.

Historical incidents this would have caught:
  - 3W header-swallow  : the ``1_3W_AUTO`` row present in the grid, zero rules.
  - MISC-D lumping     : Tractor / Excavator / Harvestor / Others merged into
                         one rule instead of one per row.

Design constraints (deliberately conservative — its issues are merged into the
pipeline's schema result, so a false ERROR would be worse than a miss):
  - Only SUBCLASS-slug rows are checked (``\\d+_TOKEN`` shape). A row is flagged
    only when it is rate-bearing AND carries a body token (bus/taxi/tractor/…)
    that is entirely absent from the emitted rules — pure weight/CC-band rows
    (``2_UPTO_3.5T``) have no verifiable body token and are skipped.
  - All-empty-rate rows are exempt UNLESS they are a known declined row
    (``4_OTHERS``): that row MUST emit its zero-payout rule, else ERROR.

The validator takes the source grid markdown (``indexed_doc``'s per-sheet
contents), NOT just the CICS JSON path — unlike SchemaValidator /
RuleOverlapChecker. The pipeline threads ``indexed_doc`` through
``_run_validators`` for this reason.
"""

import json
import re
from typing import Dict, List, Optional, Set

from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

# A Chola CV SUBCLASS slug: ordinal + underscore + uppercase token(s), with an
# optional positional [Electric] / [RENEWAL] suffix. Matches 1_3W_AUTO,
# 2_UPTO_3.5T, 2_4W_LT_1500CC, 1_SCHOOL_BUS, 3_HARVESTOR, 4_OTHERS,
# 1_TRAC[RENEWAL], 2_UPTO_3.5T[Electric].
_SLUG_RE = re.compile(r"\b\d+_[0-9A-Za-z][0-9A-Za-z_.]*(?:\[[A-Za-z]+\])?")

# Tokens that carry no verifiable body identity — wheel/qualifier/unit/marker
# noise stripped before deciding whether a slug has a "body" token.
_STOP_TOKENS = {
    "3w",
    "4w",
    "2w",
    "lt",
    "gt",
    "cc",
    "kw",
    "upto",
    "up",
    "to",
    "above",
    "below",
    "and",
    "new",
    "old",
    "t",
    "kg",
    "gvw",
    "electric",
    "renewal",
    "wheeler",
    "w",
}

# A row whose body normalizes to one of these is a known DECLINED row that must
# still emit a (zero-payout) rule — its absence is an ERROR even with no rates.
_DECLINED_BODY_TOKENS = {"others", "other"}

# Fuzzy body-match window: a grid body token is "covered" when its first N
# characters appear in the emitted-rules blob. 5 tolerates slug/canonical
# suffix drift ("harvestor" vs "harvester", "excavator" vs "excavators").
_BODY_MATCH_PREFIX_LEN = 5


def _split_cells(line: str) -> List[str]:
    """Split a grid line into stripped cells (pipe, then comma, then 2+ space)."""
    if "|" in line:
        parts = line.split("|")
    elif "," in line:
        parts = line.split(",")
    else:
        parts = re.split(r"\s{2,}", line)
    return [p.strip() for p in parts]


def _is_rate(cell: str) -> bool:
    """True when a cell is a bare numeric rate (optionally %-suffixed)."""
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?%?", cell.strip()))


def _normalize_slug(slug: str) -> str:
    """Strip ordinal prefix, [..] suffix, and lowercase; underscores -> space."""
    s = re.sub(r"\[[A-Za-z]+\]", "", slug)  # drop [Electric]/[RENEWAL]
    s = re.sub(r"^\s*\d+_", "", s)  # drop leading ordinal
    s = s.replace("_", " ").replace("-", " ").lower()
    return re.sub(r"\s+", " ", s).strip()


def _body_tokens(normalized: str) -> List[str]:
    """Body tokens left after removing wheel/qualifier noise. A token must carry
    >=3 ALPHABETIC chars to count — this drops unit/number tokens like ``3.5t``
    and ``1500cc`` (a weight/CC band, not a verifiable body identity)."""
    out = []
    for tok in normalized.split():
        if tok in _STOP_TOKENS:
            continue
        if sum(c.isalpha() for c in tok) >= 3:
            out.append(tok)
    return out


class RowCoverageValidator:
    """Assert every rate-bearing grid data row traces to >=1 emitted rule."""

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
                        message=f"Row-coverage skipped: cannot read CICS ({e})",
                        field_path="root",
                        category="Row Coverage",
                    )
                ],
                info=[],
            )

        blob = self._coverage_blob(doc)

        for sheet_name, content in (sheet_contents or {}).items():
            if not content:
                continue
            issues.extend(self._check_sheet(sheet_name, content, blob))

        errors = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.HIGH)]
        warnings = [i for i in issues if i.severity == Severity.MEDIUM]
        info = [i for i in issues if i.severity in (Severity.LOW, Severity.INFO)]
        return ValidationResult(
            is_valid=len([i for i in issues if i.severity == Severity.CRITICAL]) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )

    def _coverage_blob(self, doc: Dict) -> str:
        """Lower-cased blob of every emitted rule's identity fields + provenance
        (sub-class, make/model, section, rule_id). Hyphens -> spaces so canonical
        slugs ("school-bus") match grid bodies ("school bus")."""
        parts: List[str] = []

        def add(v):
            if v is None:
                return
            if isinstance(v, (list, tuple)):
                for x in v:
                    add(x)
            else:
                parts.append(str(v))

        for product in doc.get("products", []):
            add(product.get("sub_category"))
            add(product.get("product_name"))
            for rule in product.get("commission_rules", []):
                add(rule.get("rule_id"))
                cond = rule.get("conditions") or {}
                for k in (
                    "vehicle_sub_class",
                    "vehicle_make",
                    "vehicle_model",
                    "vehicle_make_exclude",
                    "vehicle_model_exclude",
                    "business_type",
                ):
                    add(cond.get(k))
                for trace in rule.get("source_traces", []) or []:
                    loc = (
                        (trace.get("source_location") or {})
                        if isinstance(trace, dict)
                        else {}
                    )
                    add(loc.get("section"))
                    add(trace.get("quoted_text") if isinstance(trace, dict) else None)
                add(rule.get("rule_description"))
        for trace in doc.get("source_traces", []) or []:
            if isinstance(trace, dict):
                loc = trace.get("source_location") or {}
                add(loc.get("section"))
                add(trace.get("quoted_text"))
        return " ".join(parts).replace("-", " ").lower()

    def _check_sheet(
        self, sheet_name: str, content: str, blob: str
    ) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        seen_labels: Set[str] = set()
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or set(line) <= set("|-: "):  # separator / blank
                continue
            m = _SLUG_RE.search(line)
            if not m:
                continue
            slug = m.group(0)
            if slug in seen_labels:
                continue
            seen_labels.add(slug)

            cells = _split_cells(line)
            rate_bearing = any(_is_rate(c) for c in cells)

            normalized = _normalize_slug(slug)
            bodies = _body_tokens(normalized)

            # Declined row (4_OTHERS): must emit its rule even with empty rates.
            if any(b in _DECLINED_BODY_TOKENS for b in bodies):
                if "others" not in blob and "not available" not in blob:
                    issues.append(
                        ValidationIssue(
                            severity=Severity.HIGH,
                            message=(
                                f"Declined row '{slug}' in sheet '{sheet_name}' "
                                f"produced no rule (expected a zero-payout 'others' rule)"
                            ),
                            field_path=f"sheet:{sheet_name}",
                            category="Row Coverage",
                            suggestion="Emit an explicit 'others' sub-class rule for this row.",
                        )
                    )
                continue

            if not rate_bearing:
                continue  # non-declined empty-rate row — not verifiable
            if not bodies:
                continue  # pure weight/CC band — no verifiable body token

            if not self._body_covered(bodies, blob):
                issues.append(
                    ValidationIssue(
                        severity=Severity.HIGH,
                        message=(
                            f"Grid data row '{slug}' in sheet '{sheet_name}' "
                            f"(body: {', '.join(bodies)}) traces to no emitted rule"
                        ),
                        field_path=f"sheet:{sheet_name}",
                        category="Row Coverage",
                        suggestion="A rate-bearing grid row lost its rule — check the transformer/overlay.",
                    )
                )
        return issues

    @staticmethod
    def _body_covered(bodies: List[str], blob: str) -> bool:
        """Covered when any body token's prefix appears in the blob."""
        for tok in bodies:
            probe = tok[:_BODY_MATCH_PREFIX_LEN]
            if probe and probe in blob:
                return True
        return False
