"""
Indicative-sheet markers
========================

Some worksheets in a carrier's workbook carry real commission rates and are
still *not the payout grid*. They say so themselves. Chola's ``Hero Cells
-June 26`` attachment is a sales one-pager whose note ends:

    Please check the payout Grid for actual payout, this sheet is just to
    identify the potential products

It produced 96 ``commercial-gcv`` rules on ``June PO Grid - Chola MS.eml`` —
68 exact duplicates of the real ``CV June 26`` grid and 28 with no counterpart
at all, including 22 rows on a degenerate ``0-0`` GVW band.

**The classifier was not wrong; the taxonomy was.** ``SheetType`` had exactly
two members, and ``sheet_classification_prompt.md`` says *"If the sheet
contains any commission rates, percentages, or amounts (no matter how small),
it is COMMISSION_DATA."* The one-pager has rates, so the LLM returned
``commission_data`` at confidence 1.0 and complied with its instructions
exactly. There was no label for *rate-bearing but self-declared
non-authoritative*.

This module is the deterministic half of the fix. The prompt now teaches the
third label, but a prose instruction in a prompt is not a contract — the same
lesson the electric make bucket and the ``4_OTHERS`` declined row already
taught this pipeline — so anything load-bearing needs a host-side net. The scan
runs over the FULL sheet CSV, not the 20-line preview the classifier sees
(``excel_to_csv._read_csv_preview``), so a disclaimer below the fold cannot
escape it.

**Carrier-agnostic, deliberately.** ``cpa_split`` draws the distinction this
follows: a rule whose OWN text states something needs no carrier gate, while
*inferring* something the text never stated is a stronger claim that does. A
sheet declaring itself non-authoritative is the first kind. A carrier that
later ships the same disclaimer gets the same treatment automatically.

The match rule
--------------

**The distinguishing property is not the phrase, it is the grammatical
subject.** A legitimate payout grid's footnote instructs the reader about
*other* content — "refer to the payout grid for RTO codes". A disclaimer makes
an assertion about *the sheet itself*. Matching on "payout grid" alone would
fire on every real payout grid there is.

So the test is a conjunction, and all three halves are load-bearing:

* **Self-reference AND deferral on the SAME physical line.** The disclaimer
  lives in one spreadsheet cell, so it is one CSV field on one line. A header
  row saying "Payout Grid" cannot combine with an unrelated footnote 400 rows
  below to manufacture a hit. A whole-blob scan with a proximity window would
  be strictly weaker and much harder to reason about.
* **A length floor.** A disclaimer is prose. A ``Payout Grid`` header cell or a
  ``This Sheet`` column label can never clear ``_MIN_LINE_LENGTH``.
* **Every deferral phrase carries its object.** Not ``"payout grid"`` but
  ``"check the payout grid for actual payout"`` — the self-negating form.

A false positive here deletes a whole sheet's rules silently, so the phrase
lists are a measured hypothesis, not a guess: they were swept against every
stored sheet in the corpus and hit exactly one. **Re-run that sweep before
adding a phrase.**

Rollback is ``CINS_INDICATIVE_SHEETS=false``, no redeploy. It must lift
*every* gate — this scan AND the enum coercion in ``sheet_classifier`` — not
just one, the same way ``CINS_SKIP_HIDDEN_SHEETS`` has to lift the rerun-pass
API rejection and not only the routing filter.
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("indicative_markers")

#: A sheet asserting something about ITSELF. Half one of the conjunction.
_SELF_REF = (
    "this sheet is",
    "this sheet only",
    "this sheet just",
    "sheet is just to",
    "sheet is only to",
    "this grid is",
    "this file is",
    "this is just a",
    "this is only a",
)

#: What it defers TO. Every entry carries the deferral object, never the bare
#: "payout grid" — which appears on every real payout grid in the corpus.
_DEFERRAL = (
    "identify the potential products",
    "check the payout grid for actual payout",
    "refer the payout grid for actual payout",
    "refer to the payout grid for actual payout",
    "not the actual payout",
    "not a payout grid",
    "not the payout grid",
    "for indicative purpose",
    "indicative only",
)

#: A disclaimer is prose. Below this, a match is a header cell or a column
#: label, not a sentence.
_MIN_LINE_LENGTH = 60

#: Beyond this, scanning costs more than the sheet is worth. Logged and
#: skipped rather than silently truncated — a truncated scan that finds
#: nothing is indistinguishable from a clean one.
_MAX_SCAN_BYTES = 8 * 1024 * 1024

#: Everything outside this survives normalisation; everything else becomes a
#: space. This is what lets the corpus's comma-jammed ``payout,this`` match.
_NON_TEXT = re.compile(r"[^a-z0-9%]+")


def indicative_markers_enabled() -> bool:
    """Whether self-declared indicative sheets are excluded from extraction.

    Rollout switch. Flipping ``CINS_INDICATIVE_SHEETS`` off restores the
    pre-change behaviour (a one-pager is classified and routed like any other
    rate-bearing sheet) without a redeploy. Read from Django settings when
    available so it can be managed like every other setting, falling back to
    the environment for the standalone CLI path where Django is not
    configured — the same shape as ``sheet_visibility.skip_hidden_sheets_enabled``.
    """
    try:
        from django.conf import settings

        return bool(getattr(settings, "CINS_INDICATIVE_SHEETS", True))
    except Exception:
        raw = os.environ.get("CINS_INDICATIVE_SHEETS", "").strip().lower()
        return raw not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class IndicativeMarkerMatch:
    """One line that declares its sheet is not the authoritative payout grid.

    Carries both matched substrings and the raw excerpt because this is the
    only evidence a reviewer gets for why a sheet's rules vanished. It is
    recorded into the classification entry, which reaches the frontend
    verbatim through the free-form ``sheet_metadata`` JSONB.
    """

    self_ref: str
    deferral: str
    excerpt: str
    line_number: int


def _normalize(line: str) -> str:
    """Fold one physical CSV line to matchable text.

    Casefold, then collapse every non-alphanumeric run to a single space. The
    corpus writes ``for actual payout,this sheet is just to identify`` with no
    space after the comma, so splitting on punctuation rather than stripping
    it is what makes the phrase contiguous.
    """
    return _NON_TEXT.sub(" ", line.casefold()).strip()


def scan_for_indicative_marker(csv_text: str) -> Optional[IndicativeMarkerMatch]:
    """The first line declaring this sheet is not the payout grid, or ``None``.

    See the module docstring for why the test is a same-line conjunction with a
    length floor rather than a phrase list. Returns on the first hit: one
    disclaimer is as conclusive as ten, and the excerpt of the first is the one
    a reviewer wants to read.
    """
    if not csv_text or not indicative_markers_enabled():
        return None

    for index, raw_line in enumerate(csv_text.splitlines(), start=1):
        normalized = _normalize(raw_line)
        if len(normalized) < _MIN_LINE_LENGTH:
            continue

        self_ref = next((p for p in _SELF_REF if p in normalized), None)
        if self_ref is None:
            continue

        deferral = next((p for p in _DEFERRAL if p in normalized), None)
        if deferral is None:
            continue

        return IndicativeMarkerMatch(
            self_ref=self_ref,
            deferral=deferral,
            excerpt=raw_line.strip()[:240],
            line_number=index,
        )

    return None


def scan_csv_file(csv_path: Union[str, Path]) -> Optional[IndicativeMarkerMatch]:
    """:func:`scan_for_indicative_marker` over a CSV on disk.

    Fails open in every direction — an unreadable, missing or oversized file
    yields ``None``, so this filter can never drop a sheet on the basis of
    information it does not have. That is the same discipline
    ``sheet_visibility`` applies to a classification entry with no
    ``visibility`` key.
    """
    if not indicative_markers_enabled():
        return None

    path = Path(csv_path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning("Could not stat '%s' for indicative scan: %s", path, exc)
        return None

    if size > _MAX_SCAN_BYTES:
        logger.warning(
            "Skipping indicative scan of '%s': %d bytes exceeds the %d-byte cap",
            path,
            size,
            _MAX_SCAN_BYTES,
        )
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Could not read '%s' for indicative scan: %s", path, exc)
        return None

    return scan_for_indicative_marker(text)
