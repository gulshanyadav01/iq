"""
Chola Product Hierarchy Validator

Validates each Chola rule's ``(product, sub_product, vehicle_sub_class)``
combination against the seeded ``CarrierProductHierarchyMaster`` (3A), and
enforces that ``vehicle_sub_class`` is PRESENT on every Chola rule — for this
carrier it is the axis deals are keyed on, so a rule without one is a HIGH
error (fails validation, blocks the CICS output-cache pin) rather than a
silently-green omission. The transformer backfills what it can derive from the
product/segment label first; only genuinely underivable rules reach here.
Validation-only: it does not touch extraction, and it reuses the existing
``VEHICLE_SUB_CLASS_MAPPINGS`` spelling normalization (2A) rather than
building a second canonicalization scheme — the master's raw
``vehicle_subclass`` text is normalized through the SAME dict the transformer
used to produce ``conditions.vehicle_sub_class``, so both sides land in the
identical slug space and a master row whose spelling the transformer could
never match (e.g. a source typo the dict has no key for) simply drops out of
the valid set instead of producing a mismatched comparison.

Gated to Chola documents (``provider.provider_id == "Chola"``). The *validity*
check is additionally gated to products the master actually covers — a product
bucket absent from the master (e.g. no rows for it) is skipped rather than
flagged, since that is missing coverage, not a validity violation. The
*mandatory-presence* check is not: it needs no master coverage to know that an
empty ``vehicle_sub_class`` is wrong.

**GCV is exempt from the mandatory-presence check.** A goods-carrying grid
prices by weight band and publishes no body type, so the transformer clears the
field outright (``_strip_gcv_subclass``) and absence is the correct shape, not a
gap. Without the carve-out this HIGH would fire on every GCV rule of every Chola
document — and a HIGH blocks ``cics_cache.should_cache`` from pinning the
output, making the carrier's whole CV book permanently un-cacheable.

The master's raw ``vehicle_subclass`` text needs the SAME regex
substring/word-boundary matching the transformer runs (``_normalize_list_field``),
not a naive exact-key lookup: the source spells some values without the
mapping dict's spacing (e.g. "PrivateCar" vs. the dict's "private car" key,
"Pickup/ Delivery Van" vs. "pick up/delivery van"). A cheap camelCase-split
pass ("PrivateCar" -> "PrivateCar" -> "Private Car") closes that gap; a
handful of the source's OWN typos (e.g. "Excavaters", "Tranist Mixer") still
fail to normalize even after that, which is correct and self-consistent — a
grid rule spelling the same typo would fail through the identical dict in
the transformer, so it could never produce a matching slug either.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Set, Tuple

from cins.processors.core.carrier_subclasses import (
    canonical_subclass_slugs,
    carrier_subclass_rows,
)
from cins.processors.core.raw_to_cics_transformer import (
    VEHICLE_SUB_CLASS_MAPPINGS,
    RawToCICSTransformer,
    product_subproduct_bucket,
)
from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

# Product-subproduct (Product.product_subproduct) -> the master's product
# bucket. The mapping itself lives in raw_to_cics_transformer as
# ``product_subproduct_bucket`` — ONE definition shared with the transformer
# (which canonicalizes make/model inside a bucket) and CholaMMVValidator, so
# no two call sites can disagree about what a product means. Kept as a local
# alias because this module refers to it in several places.
_bucket_for = product_subproduct_bucket


class CholaProductHierarchyValidator:
    """Validate Chola rules' (product, sub_product, vehicle_sub_class)
    combinations against the seeded carrier-scoped hierarchy master."""

    def __init__(self):
        self._valid_sub_products: Dict[str, Set[str]] = {}
        self._valid_subclasses: Dict[Tuple[str, str], Set[str]] = {}
        self._covered_products: Set[str] = set()
        rows = carrier_subclass_rows("Chola")

        # Reused only for its normalization method — warnings it appends are
        # discarded (this instance never feeds the pipeline), mirroring
        # StateColumnAlignmentValidator's ``self._expander`` pattern.
        normalizer = RawToCICSTransformer()

        def _normalize(text: str):
            normalizer.warnings = []
            return normalizer._normalize_list_field(
                text, VEHICLE_SUB_CLASS_MAPPINGS, "chola_hierarchy_seed"
            )

        buckets: Set[Tuple[str, str]] = set()
        for row in rows:
            product = row.get("product") or ""
            if not product:
                continue
            self._covered_products.add(product)
            sub_product = row.get("sub_product") or ""
            if sub_product:
                self._valid_sub_products.setdefault(product, set()).add(sub_product)
            buckets.add((product, sub_product))

        # One slug-resolution path, shared with the transformer's MISC-D merge
        # (``_canonical_subclass_slugs``) — a second scheme here would flag
        # spellings the transformer would have folded. ``drop_sentinels=False``
        # because "Not Available" is a real master value here: recording the
        # absence of a body is valid, even though the sentinel can never belong
        # in a body list the transformer widens a rule to.
        for product, sub_product in buckets:
            slugs = canonical_subclass_slugs(
                "Chola",
                product,
                sub_product,
                _normalize,
                rows=rows,
                drop_sentinels=False,
            )
            if slugs:
                self._valid_subclasses.setdefault((product, sub_product), set()).update(
                    slugs
                )

    def validate(self, cics_path: str) -> ValidationResult:
        try:
            with open(cics_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            return self._result(
                [
                    ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"Chola product hierarchy validator skipped: cannot read CICS ({e})",
                        field_path="root",
                        category="Chola Product Hierarchy",
                    )
                ]
            )

        if not self._covered_products:
            return self._result([])  # master unavailable/unseeded — fail open

        provider = doc.get("provider") or {}
        if provider.get("provider_id") != "Chola":
            return self._result([])

        findings: Dict[Tuple[str, str, Optional[str], str], ValidationIssue] = {}
        for product in doc.get("products", []):
            bucket = _bucket_for(product.get("product_subproduct"))
            if bucket is None:
                continue  # unclassifiable subproduct — nothing to say about it

            subproduct = product.get("product_subproduct")
            sub_category = (product.get("sub_category") or "").strip()
            sub_product = sub_category if bucket == "CV" else ""
            seg = f"{bucket}/{sub_product}" if sub_product else bucket

            # Mandatory-ness first: it is checked BEFORE the coverage and
            # sub-product guards below, because a rule with no sub-class at all
            # is a defect regardless of whether the master can vouch for this
            # exact (product, sub_product) combination. The validity checks
            # that follow are what need master coverage; this one does not.
            #
            # GCV is carved out: a goods-carrying grid has no body dimension at
            # all (see ``_strip_gcv_subclass``), so absence there is correct
            # rather than a gap. Gated on ``product_subproduct``, matching the
            # transformer's own gate — ``sub_category`` is free text.
            missing = [
                rule.get("rule_id") or "<no rule_id>"
                for rule in product.get("commission_rules", [])
                if not (rule.get("conditions") or {}).get("vehicle_sub_class")
            ]
            if subproduct == "commercial-gcv":
                missing = []
            if missing:
                key = (bucket, sub_product, None, "missing")
                if key not in findings:
                    shown = ", ".join(missing[:5])
                    more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
                    valid = sorted(
                        self._valid_subclasses.get((bucket, sub_product), set())
                    )
                    findings[key] = ValidationIssue(
                        severity=Severity.HIGH,
                        message=(
                            f"Chola product hierarchy: vehicle_sub_class is "
                            f"mandatory but missing on {len(missing)} rule(s) "
                            f"for {seg}: {shown}{more}"
                        ),
                        field_path=(
                            "products[].commission_rules[].conditions.vehicle_sub_class"
                        ),
                        category="Chola Product Hierarchy",
                        suggestion=(
                            f"Valid vehicle_sub_class for {seg}: {valid}"
                            if valid
                            else (
                                "Populate vehicle_sub_class from the grid's "
                                "SUBCLASS label for this segment"
                            )
                        ),
                    )

            if bucket not in self._covered_products:
                continue  # master says nothing about this bucket — no validity check

            valid_sub_products = self._valid_sub_products.get(bucket, set())
            if (
                sub_product
                and valid_sub_products
                and sub_product not in valid_sub_products
            ):
                key = (bucket, sub_product, None, "sub_product")
                if key not in findings:
                    findings[key] = ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=(
                            f"Chola product hierarchy: unknown sub-product "
                            f"'{sub_product}' for product '{bucket}'"
                        ),
                        field_path="products[].sub_category",
                        category="Chola Product Hierarchy",
                        suggestion=(
                            f"Valid sub-products for {bucket}: "
                            f"{sorted(valid_sub_products)}"
                        ),
                    )
                continue  # sub-product itself invalid; subclass check moot

            valid_subclasses = self._valid_subclasses.get((bucket, sub_product), set())
            if not valid_subclasses:
                continue  # no coverage for this exact combo — not a violation

            for rule in product.get("commission_rules", []):
                conditions = rule.get("conditions") or {}
                subclasses = conditions.get("vehicle_sub_class") or []
                if not isinstance(subclasses, list):
                    subclasses = [subclasses]
                for sc in subclasses:
                    if sc and sc not in valid_subclasses:
                        key = (bucket, sub_product, sc, "invalid")
                        if key not in findings:
                            findings[key] = ValidationIssue(
                                severity=Severity.MEDIUM,
                                message=(
                                    f"Chola product hierarchy: vehicle_sub_class "
                                    f"'{sc}' is not valid for {seg}"
                                ),
                                field_path=(
                                    "products[].commission_rules[]"
                                    ".conditions.vehicle_sub_class"
                                ),
                                category="Chola Product Hierarchy",
                                suggestion=(
                                    f"Valid vehicle_sub_class for {seg}: "
                                    f"{sorted(valid_subclasses)}"
                                ),
                            )

        return self._result(list(findings.values()))

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
