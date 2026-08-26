"""
Sheet visibility
================

Excel workbooks carry worksheets whose tab is hidden (``sheet_state ==
"hidden"``) or hidden-and-not-unhideable-from-the-UI (``"veryHidden"``). Those
tabs are scratch pads, templates and stale lookup grids the author deliberately
took off the tab bar. They must never produce commission rules, and must never
appear in a Pass 1/2/3 prompt.

Hidden sheets are still indexed, converted and classified. That is deliberate:
the UI needs to show *what* was skipped, and "hidden sheet the classifier
tagged ``commission_data``" is the signal that the wrong tab was hidden. The
exclusion happens at the point of *use*, not at enumeration::

    workbook ──▶ ExcelToCSVConverter.process_sheet() captures sheet_state
                     │
                     ▼
        indexer writes sheet_classifications[name]["visibility"]
                     │
                     ▼ persisted to IndexedContent.sheet_metadata["classifications"]
                     │
       ┌─────────────┼───────────────┬─────────────────┬──────────────────┐
       ▼             ▼               ▼                 ▼                  ▼
  derive_pipeline  get_commission  get_master    rerun_single_pass   _run_validators
  _routing         _sheets         _sheets       (tasks.py)          (pipeline.py)
  (pipeline.py)    (indexer.py)    (indexer.py)

  plus: the Pass 0B/0C skip in the indexer, the rerun-pass API guard, the
  all-hidden check in run_extraction, and the export's coverage sheet.

**The invariant, not the count:** every consumer derives sheet sets from the
helpers in this module. Do not inline the visibility comparison anywhere — the
moment two sites compute it differently, the pipeline routes a different sheet
set than the one it pre-created PENDING ExtractionPass rows for, and the UI
shows passes that never complete.

**Fail open.** A classification entry with no ``visibility`` key — every
document indexed before ``INDEXER_VERSION`` 1.4, plus the ``image::`` pseudo
sheets that have no workbook behind them — reads as *visible*. This filter can
therefore never drop data on the basis of information it does not have.

**The second exclusion axis: self-declared indicative sheets.** A sheet can be
visible, classified, rate-bearing, and still not be the payout grid, because
its own text says so (Chola's ``hero cells`` one-pager: *"this sheet is just to
identify the potential products"*). Those are labelled ``indicative_data`` and
routed NOWHERE — not to extraction, and deliberately not to ``master_data``
either, since master sheets are concatenated into ``master_context`` and
injected into every other sheet's Pass 1 and Pass 2 prompt.

That axis has a trap the visibility axis does not. ``derive_pipeline_routing``
falls back to "process every visible sheet" when *no* sheet was classified
``commission_data`` — so a document whose only rate-bearing sheet is a
one-pager would be routed anyway, inverting the exclusion. Use
:func:`routable_sheet_names` (hidden **and** indicative removed) for that
fallback, never :func:`visible_sheet_names`.
"""

import logging
import os
from typing import Any, Dict, Iterable, List, NamedTuple, Optional

logger = logging.getLogger("sheet_visibility")

#: openpyxl's ``Worksheet.sheet_state`` value for a normal, on-the-tab-bar sheet.
#: The other two states are ``"hidden"`` and ``"veryHidden"``.
VISIBLE_SHEET_STATE = "visible"

COMMISSION_DATA = "commission_data"
MASTER_DATA = "master_data"

#: A sheet that carries real rates and still is not the payout grid, because
#: it says so itself. Routed NOWHERE — see the module docstring and
#: ``processors/extractors/indicative_markers.py``.
INDICATIVE_DATA = "indicative_data"


def skip_hidden_sheets_enabled() -> bool:
    """Whether hidden sheets are excluded from extraction (default: yes).

    Rollout switch. Flipping ``CINS_SKIP_HIDDEN_SHEETS`` off restores the
    pre-change behaviour (every indexed sheet is routable) without a redeploy.
    Read from Django settings when available so it can be managed like every
    other setting, falling back to the environment for the standalone CLI path
    where Django is not configured.
    """
    try:
        from django.conf import settings

        return bool(getattr(settings, "CINS_SKIP_HIDDEN_SHEETS", True))
    except Exception:
        raw = os.environ.get("CINS_SKIP_HIDDEN_SHEETS", "").strip().lower()
        return raw not in ("0", "false", "no", "off")


def sheet_state(info: Optional[Dict[str, Any]]) -> str:
    """The recorded tab state for one classification entry.

    Absent key => ``"visible"`` (see the fail-open note in the module docstring).
    """
    return (info or {}).get("visibility") or VISIBLE_SHEET_STATE


def is_sheet_visible(info: Optional[Dict[str, Any]]) -> bool:
    """Whether a sheet may take part in extraction.

    Whitelist, not blacklist: only ``"visible"`` passes, so a future openpyxl
    state we have never seen is treated as hidden rather than silently routed.
    """
    if not skip_hidden_sheets_enabled():
        return True
    return sheet_state(info) == VISIBLE_SHEET_STATE


def sheet_type_of(info: Optional[Dict[str, Any]]) -> Optional[str]:
    """Classification label for one entry, or ``None`` when unclassified."""
    return (info or {}).get("sheet_type")


class SheetSets(NamedTuple):
    """The four buckets every routing decision is made from.

    **``indicative`` is appended LAST, and that ordering is load-bearing.**
    Two production call sites unpack this positionally as a 3-tuple. With the
    new field last, a stale unpack raises ``ValueError`` immediately and
    loudly. Inserted anywhere earlier, ``hidden`` would silently bind to the
    indicative list and hidden-sheet reporting would go wrong everywhere with
    no error at all. Do not "tidy" this by grouping the classification buckets
    together — ``test_sheet_type_rollout`` pins the order for this reason.
    """

    commission: List[str]
    master: List[str]
    hidden: List[str]
    indicative: List[str]


def derive_sheet_sets(
    sheet_classifications: Optional[Dict[str, Any]],
) -> SheetSets:
    """Split classified sheets into ``(commission, master, hidden, indicative)``.

    Single source of truth for sheet routing. ``commission``, ``master`` and
    ``indicative`` contain visible sheets only — a hidden sheet appears in
    ``hidden`` and nowhere else, whatever it was classified as.

    Returns a :class:`SheetSets` named tuple, so call sites read ``.commission``
    / ``.master`` / ``.hidden`` / ``.indicative`` rather than an unlabelled index.
    """
    commission: List[str] = []
    master: List[str] = []
    hidden: List[str] = []
    indicative: List[str] = []

    # Resolved once rather than per sheet — it reads Django settings.
    skipping = skip_hidden_sheets_enabled()

    for name, info in (sheet_classifications or {}).items():
        if skipping and sheet_state(info) != VISIBLE_SHEET_STATE:
            hidden.append(name)
            continue
        stype = sheet_type_of(info)
        if stype == COMMISSION_DATA:
            commission.append(name)
        elif stype == MASTER_DATA:
            master.append(name)
        elif stype == INDICATIVE_DATA:
            indicative.append(name)

    return SheetSets(commission, master, hidden, indicative)


def hidden_sheet_names(
    sheet_classifications: Optional[Dict[str, Any]],
) -> List[str]:
    """Names of sheets excluded from extraction because their tab is hidden."""
    return derive_sheet_sets(sheet_classifications).hidden


def visible_sheet_names(
    sheet_names: Optional[Iterable[str]],
    sheet_classifications: Optional[Dict[str, Any]],
) -> List[str]:
    """``sheet_names`` minus the hidden ones, order preserved.

    Used for the "no commission sheets classified — process everything"
    fallback, which would otherwise resurrect every hidden sheet it was meant
    to exclude.
    """
    hidden = set(hidden_sheet_names(sheet_classifications))
    return [name for name in (sheet_names or []) if name not in hidden]


def indicative_sheet_names(
    sheet_classifications: Optional[Dict[str, Any]],
) -> List[str]:
    """Names of visible sheets that declare they are not the payout grid."""
    return derive_sheet_sets(sheet_classifications).indicative


def routable_sheet_names(
    sheet_names: Optional[Iterable[str]],
    sheet_classifications: Optional[Dict[str, Any]],
) -> List[str]:
    """``sheet_names`` minus hidden AND minus self-declared indicative ones.

    This exists for the "no commission sheets classified — process everything"
    fallback, and filtering only the *commission* set is not enough there. A
    document whose sole rate-bearing sheet is a sales one-pager classifies it
    ``indicative_data``, finds an empty commission set, falls back — and routes
    the very sheet the classification existed to exclude, inverting the
    feature exactly.

    :func:`visible_sheet_names` is deliberately left alone: it means "visible",
    which is a different question, and other callers depend on that meaning.
    """
    excluded = set(hidden_sheet_names(sheet_classifications))
    excluded.update(indicative_sheet_names(sheet_classifications))
    return [name for name in (sheet_names or []) if name not in excluded]


def describe_hidden(
    sheet_classifications: Optional[Dict[str, Any]],
) -> str:
    """``"Scratch (hidden), Internal (veryHidden)"`` — for log lines."""
    classifications = sheet_classifications or {}
    return ", ".join(
        f"{name} ({sheet_state(classifications.get(name))})"
        for name in hidden_sheet_names(classifications)
    )


def nothing_routable_error(
    total_sheets: int,
    hidden_names: Iterable[str],
    indicative_names: Optional[Iterable[str]] = None,
) -> str:
    """Message for the terminal "nothing left to extract" case.

    Reached when every sheet in the workbook is either hidden or self-declared
    indicative. Completing with zero rules would read in the UI as a successful
    extraction of a document that happens to have no commission data — the one
    outcome this whole guard exists to prevent.

    The message names whichever cause(s) actually apply. Reporting "all sheets
    are hidden" for a document whose sheets are merely indicative would send
    the operator to unhide a tab that was never hidden.
    """
    hidden = list(hidden_names or [])
    indicative = list(indicative_names or [])

    # Hidden-only keeps its original wording verbatim. It is the overwhelmingly
    # common case, it is what the operator-facing docs quote, and it is pinned
    # by AllHiddenTests.
    if not indicative:
        names = ", ".join(hidden)
        return (
            f"Nothing to extract — all {total_sheets} sheet(s) in this document "
            f"are hidden: {names}. Unhide the relevant tab(s) in Excel and "
            f"re-upload, or re-index the document."
        )

    causes = []
    if hidden:
        causes.append(f"{len(hidden)} hidden ({', '.join(hidden)})")
    causes.append(
        f"{len(indicative)} self-declared as not the payout grid "
        f"({', '.join(indicative)})"
    )

    remedy = (
        "Unhide the relevant tab(s) in Excel and re-upload, or re-index the document."
        if hidden
        else (
            "These sheets state their rates are not the payout to be used; "
            "supply the actual payout grid, or re-index the document."
        )
    )
    return (
        f"Nothing to extract — of {total_sheets} sheet(s) in this document, "
        f"{' and '.join(causes)}. {remedy}"
    )


def all_hidden_error(total_sheets: int, hidden_names: Iterable[str]) -> str:
    """Back-compat alias for :func:`nothing_routable_error`, hidden cause only.

    Kept because it is referenced by name in ``.claude/rules/cins.md`` and by
    the hidden-sheet test suite.
    """
    return nothing_routable_error(total_sheets, hidden_names)
