"""
P5: the inherits_from_previous early-exit must not skip a document that actually
ships a commission grid. Chola June'26 forwarded a "continuing the same grid as
May 2026" cover email while still attaching complete CV / Private Car / TW grids;
Pass 1 set inherits_from_previous=True and Pass 2/3 were skipped, yielding an
empty document with no previous_document_id to copy from.

_commission_grid_present gates the skip on the indexer's "[type: commission]"
table marker so the attached grid is extracted instead of dropped.

Run from the backend container:
    python manage.py test cins.tests.test_inheritance_guard -v 2
"""

from unittest import TestCase

from cins.processors.core.llm_extract import _commission_grid_present


class CommissionGridPresentTests(TestCase):
    def test_grid_marker_present(self):
        content = (
            "## Table 1: GCCV Commission [type: commission]\n"
            "| PROD > GCCV | AP > ACT > 0.325 | AP > PACK > 0.375 |"
        )
        self.assertTrue(_commission_grid_present(content))

    def test_bare_no_change_email_has_no_grid(self):
        content = (
            "EMAIL BODY:\nDear team, we are continuing with the same grid "
            "that was applicable for May 2026. Regards."
        )
        self.assertFalse(_commission_grid_present(content))

    def test_empty_and_none(self):
        self.assertFalse(_commission_grid_present(""))
        self.assertFalse(_commission_grid_present(None))

    def test_master_only_content_is_not_a_grid(self):
        # A master-data-only sheet (RTO list) carries no commission marker.
        content = "## Table 1: RTO List [type: master_data]\n| AP | Andhra |"
        self.assertFalse(_commission_grid_present(content))
