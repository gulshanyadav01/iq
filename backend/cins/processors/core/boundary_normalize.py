"""Re-run the evidence-gated CC/GVW boundary normalization on the MERGED doc.

``RawToCICSTransformer._normalize_boundary_overlaps`` closes the gap left by
``_dedupe_range_overlaps`` when a range ladder is fragmented across states /
RTOs so the per-group bump cannot see the lower rung. But every one of its
three invocations lives inside ``_transform_product``, which — for a workbook
processed ``sheet_by_sheet`` — runs **once per sheet**. A ladder fragmented
across *sheets* therefore never gets normalized at all: the sheet holding the
upper rungs has no lower rung to use as evidence, and by the time
``pipeline._merge_sheet_jsons`` concatenates the sheets, nothing runs again.

Measured on Chola "PO Grid-Apr 26.eml" (doc 1457d5e8): the ``Act Only
Policy-Changes`` sheet publishes a partial GCV weight table that *starts* at
``40T_TO_43T``, so its 7 third-party rules (one per state) shipped
``(40000, 43000)`` — overlapping by one point the ``20T_TO_40T`` rung that
lives in the ` CVApr26` sheet and ends at 40000. That sheet's own ladder is
internally consistent (``40001-43000``, ``43001-47500``, ``47501-999999``),
which is exactly why the defect reads as a lone stray band in the export.

Only the evidence-gated pass is re-run here. ``_dedupe_range_overlaps`` is an
unconditional ladder walk and is deliberately NOT re-run at document level:
merging two sheets' tables into one group could shift bands that share no real
tier edge. ``_normalize_boundary_overlaps`` bumps a start only when it already
coincides with a compatible band's end, and it is idempotent, so re-running it
on a document whose sheets were each already normalized is a strict no-op.
"""

import logging
from types import SimpleNamespace

from cins.schema.cics_model import RuleConditions

from .raw_to_cics_transformer import RawToCICSTransformer

logger = logging.getLogger(__name__)

# Prefix so a document-level bump is distinguishable from a sheet-level one in
# the CICS result's notices.
_NOTICE_PREFIX = "Merged-document "


def materialize_boundary_normalization(cics_doc, notices=None):
    """Normalize range boundaries across the merged document, in place.

    Mutates and returns ``cics_doc``. Grouping is per product, matching the
    per-sheet semantics — a range ladder never spans products. Only the range
    ints move; every other key of every rule dict is untouched.

    ``notices``, when given, is extended with the transformer's own notices.
    """
    if not isinstance(cics_doc, dict):
        return cics_doc

    range_fields = RawToCICSTransformer._DEDUPE_RANGES

    for product in cics_doc.get("products") or []:
        rules = product.get("commission_rules")
        if not rules:
            continue

        shims = []
        owners = []
        for rule in rules:
            conditions = rule.get("conditions")
            if not isinstance(conditions, dict):
                continue
            try:
                parsed = RuleConditions(**conditions)
            except Exception:  # pragma: no cover - defensive
                # A rule whose conditions don't parse is left exactly as it is;
                # it is not this pass's job to reject a document.
                logger.warning(
                    "boundary_normalize: skipping unparsable conditions on rule %s",
                    rule.get("rule_id"),
                )
                continue
            shims.append(SimpleNamespace(conditions=parsed))
            owners.append(conditions)

        if not shims:
            continue

        transformer = RawToCICSTransformer()
        transformer._normalize_boundary_overlaps(shims)

        for shim, conditions in zip(shims, owners):
            for start_field, end_field in range_fields:
                for field in (start_field, end_field):
                    new = getattr(shim.conditions, field, None)
                    if new != conditions.get(field):
                        conditions[field] = new

        if notices is not None:
            notices.extend(f"{_NOTICE_PREFIX}{n}" for n in transformer.notices)
        for note in transformer.notices:
            logger.info("boundary_normalize: %s", note)

    return cics_doc
