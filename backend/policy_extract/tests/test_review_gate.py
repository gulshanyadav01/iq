"""The confidence gate that assigns review_status on completion.

`_compute_review_status` decides AUTO_APPROVED vs NEEDS_REVIEW for a completed
extraction: any extracted field below the confidence threshold, or any field the
resolver flagged do_verification="TRUE", routes the row to the human review queue
("no silent issuance"). Empty/un-extracted fields are ignored so a sparse-but-
confident document still auto-approves.

Run from the backend container:
    python manage.py test policy_extract.tests.test_review_gate -v 2
"""

from django.test import SimpleTestCase, override_settings

from policy_extract.models import PolicyExtractionRequest
from policy_extract.tasks import _compute_review_status, _normalize_confidence

RS = PolicyExtractionRequest.ReviewStatus


def _f(value, conf, verify=False, extracted=True):
    return {
        "value": value,
        "confidence_score": conf,
        "is_extracted": "TRUE" if extracted else "FALSE",
        "do_verification": "TRUE" if verify else "FALSE",
    }


class NormalizeConfidenceTests(SimpleTestCase):
    def test_zero_to_one_passthrough(self):
        self.assertEqual(_normalize_confidence(0.82), 0.82)

    def test_percentage_scale_divided(self):
        self.assertAlmostEqual(_normalize_confidence(82), 0.82)

    def test_unusable_returns_none(self):
        self.assertIsNone(_normalize_confidence(None))
        self.assertIsNone(_normalize_confidence("n/a"))


@override_settings(
    POLICY_EXTRACT_REVIEW_ENABLED=True,
    POLICY_EXTRACT_REVIEW_CONFIDENCE_THRESHOLD=0.7,
)
class ComputeReviewStatusTests(SimpleTestCase):
    def test_all_high_confidence_auto_approves(self):
        result = {
            "extracted_fields": {
                "policy_number": _f("P1", 0.98),
                "cust_name": _f("ASHA", 0.91),
            }
        }
        self.assertEqual(_compute_review_status(result), RS.AUTO_APPROVED)

    def test_low_confidence_field_needs_review(self):
        result = {
            "extracted_fields": {
                "policy_number": _f("P1", 0.98),
                "reg_num": _f("MH12XY7788", 0.49),
            }
        }
        self.assertEqual(_compute_review_status(result), RS.NEEDS_REVIEW)

    def test_do_verification_flag_needs_review(self):
        # Flagged even though its confidence sits above the threshold.
        result = {
            "extracted_fields": {
                "product_id": _f("CV", 0.9, verify=True),
            }
        }
        self.assertEqual(_compute_review_status(result), RS.NEEDS_REVIEW)

    def test_empty_unextracted_fields_ignored(self):
        # Backfilled empties (confidence 0.0, not extracted) must not force review.
        result = {
            "extracted_fields": {
                "policy_number": _f("P1", 0.95),
                "nomineeName": _f("", 0.0, extracted=False),
                "financialInstitutionName": _f(None, 0.0, extracted=False),
            }
        }
        self.assertEqual(_compute_review_status(result), RS.AUTO_APPROVED)

    def test_threshold_boundary_is_inclusive(self):
        # Exactly at the threshold is acceptable (not below).
        result = {"extracted_fields": {"x": _f("v", 0.7)}}
        self.assertEqual(_compute_review_status(result), RS.AUTO_APPROVED)

    @override_settings(POLICY_EXTRACT_REVIEW_ENABLED=False)
    def test_gate_disabled_returns_none(self):
        result = {"extracted_fields": {"reg_num": _f("X", 0.1)}}
        self.assertIsNone(_compute_review_status(result))
