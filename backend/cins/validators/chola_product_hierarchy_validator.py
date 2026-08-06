"""
Chola Product Hierarchy Validator

Validates each Chola rule's ``(product, sub_product, vehicle_sub_class)``
combination against the seeded ``CarrierProductHierarchyMaster`` (3A).
Validation-only: it does not touch extraction, and it reuses the existing
``VEHICLE_SUB_CLASS_MAPPINGS`` spelling normalization (2A) rather than
building a second canonicalization scheme — the master's raw
``vehicle_subclass`` text is normalized through the SAME dict the transformer
used to produce ``conditions.vehicle_sub_class``, so both sides land in the
identical slug space and a master row whose spelling the transformer could
never match (e.g. a source typo the dict has no key for) simply drops out of
the valid set instead of producing a mismatched comparison.

Gated to Chola documents (``provider.provider_id == "Chola"``) and to
products the master actually covers — a product bucket absent from the
master (e.g. no rows for it) is skipped rather than flagged, since that is
missing coverage, not a validity violation.

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
import re
from typing import Dict, List, Optional, Set, Tuple

from cins.processors.core.raw_to_cics_transformer import (
    VEHICLE_SUB_CLASS_MAPPINGS,
    RawToCICSTransformer,
)
from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")

# Product-subproduct (Product.product_subproduct) -> the master's product
# bucket. Commercial variants all map to CV; everything else is looked up
# directly.
_SUBPRODUCT_TO_BUCKET = {
    "car": "Car",
    "two-wheeler": "TW",
}


def _bucket_for(product_subproduct: Optional[str]) -> Optional[str]:
    if not product_subproduct:
        return None
    if product_subproduct in _SUBPRODUCT_TO_BUCKET:
        return _SUBPRODUCT_TO_BUCKET[product_subproduct]
    if product_subproduct.startswith("commercial"):
        return "CV"
    return None


class CholaProductHierarchyValidator:
    """Validate Chola rules' (product, sub_product, vehicle_sub_class)
    combinations against the seeded carrier-scoped hierarchy master."""

    def __init__(self):
        self._valid_sub_products: Dict[str, Set[str]] = {}
        self._valid_subclasses: Dict[Tuple[str, str], Set[str]] = {}
        self._covered_products: Set[str] = set()
        try:
            from master_data.services.resolver import carrier_product_hierarchy

            rows = carrier_product_hierarchy("Chola")
        except Exception:
            rows = []

        # Reused only for its normalization method — warnings it appends are
        # discarded (this instance never feeds the pipeline), mirroring
        # StateColumnAlignmentValidator's ``self._expander`` pattern.
        normalizer = RawToCICSTransformer()

        for row in rows:
            product = row.get("product") or ""
            if not product:
                continue
            self._covered_products.add(product)
            sub_product = row.get("sub_product") or ""
            if sub_product:
                self._valid_sub_products.setdefault(product, set()).add(sub_product)
            raw_subclass = row.get("vehicle_subclass") or ""
            spaced = _CAMEL_SPLIT_RE.sub(" ", raw_subclass)
            normalizer.warnings = []
            slugs = normalizer._normalize_list_field(
                spaced, VEHICLE_SUB_CLASS_MAPPINGS, "chola_hierarchy_seed"
            )
            for slug in slugs or ():
                self._valid_subclasses.setdefault((product, sub_product), set()).add(
                    slug
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

        findings: Dict[Tuple[str, str, Optional[str]], ValidationIssue] = {}
        for product in doc.get("products", []):
            bucket = _bucket_for(product.get("product_subproduct"))
            if bucket is None or bucket not in self._covered_products:
                continue

            sub_category = (product.get("sub_category") or "").strip()
            sub_product = sub_category if bucket == "CV" else ""

            valid_sub_products = self._valid_sub_products.get(bucket, set())
            if (
                sub_product
                and valid_sub_products
                and sub_product not in valid_sub_products
            ):
                key = (bucket, sub_product, None)
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
                        key = (bucket, sub_product, sc)
                        if key not in findings:
                            seg = f"{bucket}/{sub_product}" if sub_product else bucket
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
