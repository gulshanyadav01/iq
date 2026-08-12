"""
State / RTO Presence Validator

Asserts that every commission rule exports a non-empty **State** and **RTO**
cell. Both are mandatory: no source grid in the carrier corpus prices a deal
without a geography, so a blank cell is always a pipeline defect -- either the
grid's state token was never recognised, or it was recognised but cannot be
turned into RTO codes.

Motivating incident (Chola, client-reported): 2W and commercial (GCV / PCV /
Buses / MISC-D) deals exported with blank State AND blank RTO, while Private
Car was clean. Pass 3 had extracted the state correctly on every rule; the loss
was downstream, in ``_expand_state_names``, which did not know Chola's zone
tokens ``ROM`` (Rest of Maharashtra), ``ROTN`` (Rest of Tamil Nadu) and ``NE``
(the north-east block). Each returned no canonical name, the rule's state went
None, and because the export derives the RTO column from the state whenever no
explicit ``conditions.rto`` exists, both columns went blank together. Nothing
fired: StateColumnAlignmentValidator is deliberately scoped to Private Car,
RowCoverageValidator counts a state-less row as covered, and state/rto are
unvalidated optionals on the schema. This validator is that missing net.

**The RTO check asserts the EFFECTIVE EXPORTED VALUE, not ``conditions.rto``.**
That field is legitimately empty on most rules -- the export fills the column
from the rule's states via ``MasterDataResolver.rtos_for_states``. Requiring it
directly would flag nearly every well-formed rule. So the check reproduces what
the export will render, reusing the very same ``product_bucket`` and
``rtos_for_states`` the export calls, so the two cannot disagree about what a
product name means or which RTOs a state carries.

One deliberate difference: the export additionally subtracts RTO codes a
sibling rule claims explicitly (``exclude_codes=claimed`` in ``rto_cell``, the
UP / "UP- East" complement), and this validator does NOT model that pre-pass.
The gap is one-directional and chosen: a rule whose entire state RTO set is
claimed by siblings would export blank while passing here. Replicating the
pre-pass means duplicating export-layer bookkeeping in a validator, and a miss
is the safe direction -- over-firing walls the document out of the cache.

That framing is what makes this catch the *second* failure mode: a rule with a
perfectly good state whose ``product_name`` no bucket recognises (Chola writes
its CV tables as ``GCCV`` / ``PCCV`` / ``MSV``) resolves to bucket None, and the
export renders an empty RTO cell despite the populated state.

**Severity HIGH here IS fatal**, unlike StateColumnAlignmentValidator's HIGH
which its own docstring calls "loud but not fatal". ``cics_cache.should_cache``
refuses to pin a CICS output carrying any CRITICAL/HIGH issue, so a document
with state-less rules is re-extracted rather than cached and exported blank.
That is the intended consequence of "no deal exists without a state" -- but it
means a false positive walls out a document, so every ambiguous case passes
(missing/unreadable master data disables the RTO half rather than failing it).

Rollback: ``CINS_STATE_RTO_MANDATORY=false`` disables the validator entirely
without a redeploy, mirroring ``CINS_CODEGEN_GATE`` / ``CINS_REGRESSION_GUARD``.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

from cins.processors.core.codegen_coverage import _env_flag
from cins.validators.schema_validator import (
    Severity,
    ValidationIssue,
    ValidationResult,
)

logger = logging.getLogger("cins.validators.state_rto_presence")

_CATEGORY = "State/RTO Presence"

# How many rule_ids to name in a finding before collapsing to a count.
_SHOWN = 5


def mandatory_enabled() -> bool:
    """False disables the validator entirely. Rollback without a redeploy."""
    return _env_flag("CINS_STATE_RTO_MANDATORY", True)


class StateRtoPresenceValidator:
    """Validate that every rule exports a non-empty State and RTO cell."""

    def __init__(self):
        # Resolver load is best-effort: without it the RTO half cannot be
        # evaluated, and guessing would be worse than staying quiet. The state
        # half needs no master data and runs regardless.
        try:
            from master_data.services.resolver import (
                MasterDataResolver,
                product_bucket,
            )

            self._resolver = MasterDataResolver()
            self._product_bucket = product_bucket
        except Exception:
            self._resolver = None
            self._product_bucket = None

    def validate(self, cics_path: str) -> ValidationResult:
        if not mandatory_enabled():
            # Say so. A silently-off gate is indistinguishable from a clean
            # document in the validation output, and this one is what stops a
            # blank-State export being pinned into the cache.
            logger.warning(
                "State/RTO presence check DISABLED via CINS_STATE_RTO_MANDATORY; "
                "blank State/RTO cells will not block the output-cache pin"
            )
            return self._result([])

        try:
            with open(cics_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            return self._result(
                [
                    ValidationIssue(
                        severity=Severity.MEDIUM,
                        message=f"State/RTO presence check skipped: cannot read CICS ({e})",
                        field_path="root",
                        category=_CATEGORY,
                    )
                ]
            )

        # (product label, failure kind) -> rule_ids. Grouping keeps one finding
        # per product per failure mode instead of thousands of identical rows.
        buckets: Dict[Tuple[str, str], List[str]] = {}
        # Product label -> the product_name that failed to resolve a bucket, so
        # the message can name the offending spelling.
        unresolved_names: Dict[str, str] = {}

        for product in doc.get("products", []):
            product_name = product.get("product_name") or "<unnamed>"
            sub_category = (product.get("sub_category") or "").strip()
            label = f"{product_name}/{sub_category}" if sub_category else product_name

            bucket = (
                self._product_bucket(product_name) if self._product_bucket else None
            )

            for rule in product.get("commission_rules", []):
                rule_id = rule.get("rule_id") or "<no rule_id>"
                conditions = rule.get("conditions") or {}
                states = conditions.get("state")

                if not states:
                    buckets.setdefault((label, "state"), []).append(rule_id)
                    # RTO is derived from state; reporting it too would double
                    # every finding without adding information.
                    continue

                if self._resolver is None:
                    # Master unavailable -- the RTO half is not evaluable. Say
                    # so once rather than reporting clean: a silent skip here
                    # is how a blank-RTO document gets pinned on a worker whose
                    # DB was briefly down, which is the incident this exists to
                    # prevent.
                    buckets.setdefault((label, "no_master"), []).append(rule_id)
                    continue

                if conditions.get("rto"):
                    continue  # explicit RTO wins in the export

                if bucket is None:
                    buckets.setdefault((label, "bucket"), []).append(rule_id)
                    unresolved_names[label] = product_name
                    continue

                if not isinstance(states, list):
                    states = [states]
                try:
                    filled = self._resolver.rtos_for_states(states, bucket)
                except Exception:
                    continue  # resolver failure -- stay quiet rather than wall out
                if not filled:
                    buckets.setdefault((label, "no_rto"), []).append(rule_id)

        return self._result(
            [
                self._issue(label, kind, rule_ids, unresolved_names.get(label))
                for (label, kind), rule_ids in sorted(buckets.items())
            ]
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _issue(
        label: str, kind: str, rule_ids: List[str], product_name: Optional[str]
    ) -> ValidationIssue:
        shown = ", ".join(rule_ids[:_SHOWN])
        more = f" (+{len(rule_ids) - _SHOWN} more)" if len(rule_ids) > _SHOWN else ""
        n = len(rule_ids)

        if kind == "state":
            return ValidationIssue(
                severity=Severity.HIGH,
                message=(
                    f"State is mandatory but missing on {n} rule(s) for "
                    f"{label}: {shown}{more}"
                ),
                field_path="products[].commission_rules[].conditions.state",
                category=_CATEGORY,
                suggestion=(
                    "The grid's state token was not recognised. Check the "
                    "rule's raw state_text against STATE_NAME_MAPPINGS / "
                    "STATE_GROUP_MAPPINGS in raw_to_cics_transformer, and the "
                    "carrier's zone labels against CarrierRTOZoneMaster."
                ),
            )

        if kind == "bucket":
            # MEDIUM, deliberately NOT HIGH. An unresolvable bucket is the one
            # failure mode that cannot distinguish "motor product we failed to
            # recognise" from "genuinely non-motor line on a mixed statement"
            # -- Health / Fire / Marine all resolve to None and always will.
            # A HIGH here would block the output-cache pin forever for any such
            # document, re-running full LLM codegen on every access, which is
            # exactly the false-positive-walls-out-a-document case this
            # validator's own docstring promises to avoid.
            return ValidationIssue(
                severity=Severity.MEDIUM,
                message=(
                    f"RTO will export blank on {n} rule(s) for {label}: "
                    f"product_name {product_name!r} resolves to no RTO product "
                    f"bucket, so the state-derived fill is skipped: {shown}{more}"
                ),
                field_path="products[].product_name",
                category=_CATEGORY,
                suggestion=(
                    "If this is a motor product, canonicalize product_name "
                    "(PRODUCT_NAME_MAPPINGS) or add the spelling to "
                    "_BUCKET_TOKENS in master_data.services.resolver. If it is "
                    "a non-motor line, no RTO is expected and this is noise."
                ),
            )

        if kind == "no_master":
            return ValidationIssue(
                severity=Severity.MEDIUM,
                message=(
                    f"RTO not verified on {n} rule(s) for {label}: the RTO "
                    f"master could not be loaded, so the state-derived fill "
                    f"could not be checked: {shown}{more}"
                ),
                field_path="products[].commission_rules[].conditions.rto",
                category=_CATEGORY,
                suggestion=(
                    "Check that the master-data seeder has run and the DB is "
                    "reachable from this worker."
                ),
            )

        return ValidationIssue(
            severity=Severity.HIGH,
            message=(
                f"RTO would export blank on {n} rule(s) for {label}: the rule's "
                f"states carry no product-active RTOs in the master: {shown}{more}"
            ),
            field_path="products[].commission_rules[].conditions.rto",
            category=_CATEGORY,
            suggestion=(
                "Check RTOMaster's per-product zone columns for these states -- "
                "an RTO is listed for a product only when its zone is non-null."
            ),
        )

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
