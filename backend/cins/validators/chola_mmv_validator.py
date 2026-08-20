"""
Chola Make/Model Validator

Flags ``conditions.vehicle_make`` / ``vehicle_model`` values (and their
``_exclude`` twins) that no vehicle in the seeded ``CarrierVehicleMMVMaster``
matches. It is the net under the transformer's canonicalization: by the time a
CICS doc reaches here, ``_canonicalize_make_model`` has already folded every
value it could match onto the master's spelling, so anything still unmatched is
either a make/model the master does not carry or — the case this exists for —
text that was never a vehicle at all. The extractor is known to mis-land
sub-class words and CC bands into ``vehicle_make`` (``SCOOTER``,
``150_350cc``), and until now nothing checked.

Both sides use the SAME matcher (``master_data.services.mmv``) as the
transformer, deliberately: a validator with its own matching rules would flag
values the transformer would have fixed, or stay silent on ones it could not.
That is the same discipline ``CholaProductHierarchyValidator`` follows by
reusing ``VEHICLE_SUB_CLASS_MAPPINGS``, and the product bucket comes from the
one shared ``product_subproduct_bucket``.

Everything about this validator is deliberately non-fatal:

- Findings are **MEDIUM, never HIGH or CRITICAL**. ``cics_cache.should_cache``
  refuses to pin an output carrying a CRITICAL/HIGH, so a HIGH here would wall
  a whole document out of the cache — forcing an unbounded re-extraction loop —
  over a spelling the master merely has not got yet. Unknown make/model is a
  data-quality signal for a human, not a correctness failure.
- It **fails open**. An unseeded/unreadable master, a missing index, an
  unclassifiable product, or ``CINS_CHOLA_MMV=false`` all yield a clean result
  rather than a wall of findings.
- It is gated to Chola (``provider.provider_id == "Chola"``) and to the product
  buckets the master actually covers.

Sentinels ("All Other Make/Models") are skipped: they are a deliberate bucket
marker the transformer's ``_clone_make_bucket`` fan-out depends on, not a
vehicle name.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Set, Tuple

from cins.processors.core.raw_to_cics_transformer import (
    RawToCICSTransformer,
    mmv_enabled,
    product_subproduct_bucket,
)
from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

_MAKE_FIELDS = ("vehicle_make", "vehicle_make_exclude")
_MODEL_FIELDS = ("vehicle_model", "vehicle_model_exclude")

# Cap on the values named in one message, matching the hierarchy validator's
# "first five plus a count" shape.
_MAX_SHOWN = 5


def make_model_mandatory() -> bool:
    """``CINS_MAKE_MODEL_MANDATORY`` — gates the presence half of this
    validator, following the ``CINS_STATE_RTO_MANDATORY`` precedent.

    Separate from ``CINS_CHOLA_MMV`` on purpose. That flag governs the MMV
    MASTER (canonicalize a spelling, flag one the master does not carry);
    this one governs a business rule about the EXPORT (every deal names a
    make and a model). Rolling the master back must not silently take the
    mandatory check with it, and vice versa.

    **Defaults to OFF, and the reason is the client's own data.** A dump of
    2,237 live Chola deals carries NO ``VehicleModel`` on 93% of them — 100% of
    Car/Taxi and 84% of Commercial Vehicle — and no make on only 8. Blank
    make/model is the shape their production system actually ships, so as a
    default this rule would fire on essentially every Chola document and say
    nothing a human could act on. Opt in per environment if a downstream
    consumer genuinely requires both.
    """
    raw = os.environ.get("CINS_MAKE_MODEL_MANDATORY")
    if raw is None:
        return False
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class CholaMMVValidator:
    """Flag Chola make/model values absent from the seeded MMV master."""

    def __init__(self):
        self._index: Dict[str, dict] = {}
        if not mmv_enabled():
            return
        try:
            from master_data.services.mmv import build_mmv_index

            self._index = build_mmv_index("Chola")
        except Exception:
            self._index = {}

    def validate(self, cics_path: str) -> ValidationResult:
        try:
            with open(cics_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            return self._result(
                [
                    ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"Chola MMV validator skipped: cannot read CICS ({e})",
                        field_path="root",
                        category="Chola MMV",
                    )
                ]
            )

        provider = doc.get("provider") or {}
        if provider.get("provider_id") != "Chola":
            return self._result([])

        issues: List[ValidationIssue] = []

        # Presence FIRST, and outside the master-coverage guard below: whether
        # a rule names a make at all is knowable without any seeded data, so
        # an unseeded master must not disable it. Same ordering, and the same
        # reason, as CholaProductHierarchyValidator's mandatory sub-class
        # check sitting ahead of its coverage guard.
        issues.extend(self._check_presence(doc))

        # Only "_makes" present means the master seeded no product rows at all.
        if not any(key for key in self._index if key != "_makes"):
            return self._result(issues)  # master unavailable/unseeded — fail open

        from master_data.services.mmv import match_make, match_model, scope_make

        findings: Dict[Tuple[str, str, str], ValidationIssue] = {}
        unknown: Dict[Tuple[str, str], Set[str]] = {}

        for product in doc.get("products", []):
            bucket = product_subproduct_bucket(product.get("product_subproduct"))
            if bucket is None or bucket not in self._index:
                continue  # unclassifiable, or a bucket the master cannot vouch for

            for rule in product.get("commission_rules", []):
                conditions = rule.get("conditions") or {}

                makes = _values(conditions, _MAKE_FIELDS)
                resolved = [match_make(self._index, bucket, m) for m in makes]
                for raw, hit in zip(makes, resolved):
                    if hit is None:
                        unknown.setdefault((bucket, "make"), set()).add(raw)

                # Scope models by the make via the SAME shared helper the
                # transformer canonicalized under, so the two cannot disagree
                # about what "unknown" means for a model.
                scope = scope_make(resolved)
                for raw in _values(conditions, _MODEL_FIELDS):
                    if match_model(self._index, bucket, scope, raw) is None:
                        unknown.setdefault((bucket, "model"), set()).add(raw)

        for (bucket, kind), values in unknown.items():
            ordered = sorted(values)
            shown = ", ".join(ordered[:_MAX_SHOWN])
            more = (
                f" (+{len(ordered) - _MAX_SHOWN} more)"
                if len(ordered) > _MAX_SHOWN
                else ""
            )
            field = "vehicle_make" if kind == "make" else "vehicle_model"
            findings[(bucket, kind, "unknown")] = ValidationIssue(
                severity=Severity.MEDIUM,
                message=(
                    f"Chola MMV: {len(ordered)} {kind}(s) for {bucket} match no "
                    f"vehicle in the Make/Model master: {shown}{more}"
                ),
                field_path=f"products[].commission_rules[].conditions.{field}",
                category="Chola MMV",
                suggestion=(
                    f"Confirm these are real {kind}s. A sub-class or CC band "
                    f"mis-extracted into {field} looks exactly like this; a "
                    f"genuine {kind} the master lacks should be added to "
                    f"chola_mmv.csv and reseeded."
                ),
            )

        return self._result(issues + list(findings.values()))

    @staticmethod
    def _check_presence(doc: dict) -> List[ValidationIssue]:
        """Flag rules exporting a blank VehicleMake / VehicleModel cell.

        Both columns are mandatory on the client's deal load, so a rule
        carrying neither an include nor an exclude list exports an empty cell.
        An ``_exclude`` list counts as present: the export renders it as
        ``exclude(A, B)`` (``render_make_model_cell``), so the cell is filled.

        Reported per product bucket with a count and a percentage rather than
        per rule — the failure is always structural (a whole grid has no MAKE
        column, or one merged header block lost its make), and 2,399 identical
        findings would bury the signal.

        **Severity is MEDIUM, and that is a deliberate constraint, not an
        oversight.** ``cics_cache.should_cache`` refuses to pin any output
        carrying a CRITICAL/HIGH, so raising HIGH here would wall every Chola
        document out of the cache permanently and re-run full codegen on every
        access. Measured on ``PO Grid-Apr 26.eml``: 43.5% of rules carry no
        make and 97.5% carry no model — and most of that is source-faithful
        (the Private Car grid has no MAKE or MODEL column at all; the CV grid
        prices make/model only on its up-to-3.5T rows). Until the export
        synthesises a catch-all for grids that do not grade on these axes,
        this can only be a review signal.
        """
        if not make_model_mandatory():
            return []

        issues: List[ValidationIssue] = []
        for product in doc.get("products", []):
            rules = product.get("commission_rules") or []
            if not rules:
                continue
            # Motor products only. A non-motor line (marine cargo, business
            # package) has no make or model by definition, so flagging it
            # "100% missing" would be a guaranteed false positive on every
            # such product — the same reason the unknown-value half skips a
            # bucket it cannot classify.
            if product_subproduct_bucket(product.get("product_subproduct")) is None:
                continue
            label = (
                product.get("sub_category")
                or product.get("product_subproduct")
                or "<unknown product>"
            )
            for kind, fields in (("make", _MAKE_FIELDS), ("model", _MODEL_FIELDS)):
                missing = sum(
                    1
                    for rule in rules
                    if not any((rule.get("conditions") or {}).get(f) for f in fields)
                )
                if not missing:
                    continue
                field = f"vehicle_{kind}"
                issues.append(
                    ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=(
                            f"Chola MMV: {field} is mandatory but missing on "
                            f"{missing} of {len(rules)} rule(s) "
                            f"({missing / len(rules):.1%}) for {label}"
                        ),
                        field_path=(
                            f"products[].commission_rules[].conditions.{field}"
                        ),
                        category="Chola MMV Presence",
                        suggestion=(
                            f"Check the source grid for this segment: if it has a "
                            f"{kind.upper()} column, the extraction dropped it "
                            f"(a merged header block losing its make looks like "
                            f"this). If it has none, the grid does not grade on "
                            f"{kind} and the export needs a catch-all value "
                            f"rather than a blank cell."
                        ),
                    )
                )
        return issues

    @staticmethod
    def _result(issues: List[ValidationIssue]) -> ValidationResult:
        # MEDIUM-only by construction, so errors is always empty and is_valid
        # always True — spelled out rather than assumed so a future severity
        # change has to be deliberate.
        errors = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.HIGH)]
        warnings = [i for i in issues if i.severity == Severity.MEDIUM]
        info = [i for i in issues if i.severity in (Severity.LOW, Severity.INFO)]
        return ValidationResult(
            is_valid=all(i.severity != Severity.CRITICAL for i in issues),
            errors=errors,
            warnings=warnings,
            info=info,
        )


def _values(conditions: dict, fields) -> List[str]:
    """Non-empty, non-sentinel strings across ``fields``, deduped in order."""
    out: List[str] = []
    seen: Set[str] = set()
    for field in fields:
        raw = conditions.get(field)
        if raw is None:
            continue
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            if RawToCICSTransformer._SENTINEL_MAKE_MODEL_RE.search(text):
                continue  # bucket marker, not a vehicle name
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                out.append(text)
    return out
