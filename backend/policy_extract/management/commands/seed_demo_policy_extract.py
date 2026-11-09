"""Seed completed Policy Extract demo rows — no external MinerU/LLM needed.

Creates a handful of `PolicyExtractionRequest` rows already in `completed` state,
each with a crafted `result.extracted_fields` (a realistic confidence spread),
`result.pii_masking` summary, and a synthetic source PDF (entirely FAKE data — no
real PII) stored in the object store so the document-preview pane works. The rows
span the three review states so the confidence-gated review queue has something to
demo out of the box:

    * one auto-approved (all fields high confidence)
    * two needs-review (low-confidence / do_verification-flagged fields)
    * one already approved (shows the signed-off state)

Idempotent: rows are tagged `metadata.demo_seed=true` and deleted-then-recreated
on each run.

    docker compose exec backend python manage.py seed_demo_policy_extract
"""

from __future__ import annotations

import io
import uuid
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.utils import timezone

from policy_extract.models import PolicyExtractionRequest

DEMO_MARKER = "demo_seed"


def _field(value, confidence, *, verify=False, extracted=True, edited=False):
    """Build one extracted-field entry in the pipeline's 5-key shape."""
    data = {
        "value": value,
        "confidence_score": confidence,
        "is_extracted": "TRUE" if extracted else "FALSE",
        "do_verification": "TRUE" if verify else "FALSE",
    }
    if edited:
        data["edited"] = True
    return data


def _pii(masked_count, types):
    return {"enabled": True, "masked_count": masked_count, "types": types}


def _synthetic_pdf(title: str, lines: list[str]) -> bytes:
    """Render a one-page FAKE policy document to PDF bytes via Pillow.

    Deliberately synthetic so no real PII is ever committed or stored.
    """
    from PIL import Image, ImageDraw

    # A4-ish at ~150 dpi.
    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 1200, 150], outline=(30, 64, 175), width=3)
    draw.text((70, 75), title, fill=(30, 64, 175))
    draw.text(
        (70, 110),
        "SPECIMEN — synthetic demo document, not a real policy",
        fill=(120, 120, 120),
    )
    y = 210
    for line in lines:
        draw.text((70, y), line, fill=(17, 24, 39))
        y += 46
    buffer = io.BytesIO()
    img.save(buffer, format="PDF", resolution=150.0)
    return buffer.getvalue()


# --- Demo records -----------------------------------------------------------
# Each record: filename, form_num, review_status, a {field: entry} dict, pii
# summary, the source-doc lines, and optional reviewed-age for approved rows.


def _records():
    # 1) Comprehensive private car — everything high-confidence → auto-approved.
    car_fields = {
        "policy_number": _field("POL/2026/CAR/8847121", 0.98),
        "insurer_id": _field("HDFC ERGO General Insurance Co. Ltd.", 0.97),
        "plan_type_id": _field("Comprehensive", 0.99),
        "product_id": _field("Car", 0.98),
        "business_type": _field("RENEWAL", 0.95),
        "cust_name": _field("ANANYA IYER", 0.96),
        "mobileNo": _field("9845012378", 0.94),
        "email": _field("ANANYA.IYER@EXAMPLE.COM", 0.93),
        "cust_address": _field("42 LAVELLE ROAD, BENGALURU, KARNATAKA", 0.91),
        "pinCode": _field("560001", 0.95),
        "reg_num": _field("KA01MJ4521", 0.96),
        "search_text_make": _field("HYUNDAI", 0.97),
        "model_name": _field("CRETA", 0.95),
        "variantName": _field("SX (O) 1.5 PETROL", 0.9),
        "fuel_type": _field("PETROL", 0.98),
        "VehicleClass": _field("Private Car", 0.97),
        "engineNo": _field("G4FJGM512233", 0.92),
        "chassisNo": _field("MALC381CLMM049221", 0.93),
        "cubic_capacity_value": _field("1497", 0.96),
        "registration_date": _field("14/03/2022", 0.95),
        "rto_num": _field("KA01", 0.96),
        "rto_city": _field("BENGALURU CENTRAL", 0.94),
        "rto_state": _field("KARNATAKA", 0.97),
        "sum_insured": _field("1185000", 0.95),
        "premium": _field("28470", 0.96),
        "net_premium": _field("24127", 0.95),
        "od_premium": _field("14230", 0.94),
        "tp_premium": _field("7890", 0.94),
        "ncb_percent": _field("25", 0.93),
        "serviceTax": _field("4343", 0.95),
        "policy_start_date": _field("15/03/2026", 0.97),
        "policy_end_date": _field("14/03/2027", 0.97),
        "isZeroDepCover": _field("YES", 0.92),
        "zeroDepPremium": _field("3120", 0.9),
        "intermediary_name": _field("PB PARTNERS", 0.9),
    }
    car_doc = [
        "Policy No: POL/2026/CAR/8847121        Insurer: HDFC ERGO",
        "Insured: Ananya Iyer        Mobile: 98450 12378",
        "Email: ananya.iyer@example.com",
        "Address: 42 Lavelle Road, Bengaluru, Karnataka - 560001",
        "Vehicle: Hyundai Creta SX(O) 1.5 Petrol",
        "Registration No: KA01MJ4521        Engine: G4FJGM512233",
        "Chassis: MALC381CLMM049221        CC: 1497",
        "Cover: Comprehensive (Renewal)      IDV: Rs. 11,85,000",
        "Total Premium: Rs. 28,470        NCB: 25%",
        "Period: 15/03/2026 to 14/03/2027",
    ]

    # 2) Two-wheeler — a few low-confidence / flagged fields → needs review.
    tw_fields = {
        "policy_number": _field("TW-2026-55310-AX", 0.9),
        "insurer_id": _field("ICICI Lombard General Insurance", 0.88),
        "plan_type_id": _field("Comprehensive", 0.86),
        "product_id": _field("Two Wheeler", 0.84),
        "business_type": _field("NEW", 0.82),
        "cust_name": _field("ROHIT DESHMUKH", 0.89),
        "mobileNo": _field("9920114477", 0.9),
        "email": _field("ROHIT.D@EXAMPLE.IN", 0.72),
        "cust_address": _field("FLAT 7B, SHIVAJI NAGAR, PUNE", 0.58),
        "pinCode": _field("411005", 0.63, verify=True),
        "reg_num": _field("MH12XY7788", 0.49, verify=True),
        "search_text_make": _field("HONDA", 0.91),
        "model_name": _field("ACTIVA 6G", 0.68),
        "variantName": _field("STD", 0.55),
        "fuel_type": _field("PETROL", 0.95),
        "VehicleClass": _field("Two Wheeler", 0.93),
        "engineNo": _field("JF50E1234567", 0.74),
        "chassisNo": _field("ME4JF505KMT012345", 0.66, verify=True),
        "cubic_capacity_value": _field("109", 0.9),
        "rto_num": _field("MH12", 0.8),
        "rto_city": _field("PUNE", 0.47, verify=True),
        "rto_state": _field("MAHARASHTRA", 0.86),
        "sum_insured": _field("62000", 0.88),
        "premium": _field("1420", 0.9),
        "ncb_percent": _field("0", 0.85),
        "policy_start_date": _field("02/04/2026", 0.92),
        "policy_end_date": _field("01/04/2027", 0.92),
    }
    tw_doc = [
        "Policy No: TW-2026-55310-AX      Insurer: ICICI Lombard",
        "Insured: Rohit Deshmukh      Mobile: 99201 14477",
        "Address: Flat 7B, Shivaji Nagar, Pune - 411005  (faint scan)",
        "Vehicle: Honda Activa 6G",
        "Registration No: MH12 XY 7788   <-- smudged",
        "Engine: JF50E1234567   Chassis: ME4JF505KMT012345",
        "Cover: Comprehensive (New)     IDV: Rs. 62,000",
        "Total Premium: Rs. 1,420       NCB: 0%",
        "Period: 02/04/2026 to 01/04/2027",
    ]

    # 3) Commercial vehicle — product/subclass + identifiers uncertain → needs review.
    cv_fields = {
        "policy_number": _field("CV/2026/GCV/00912", 0.9),
        "insurer_id": _field("Cholamandalam MS General Insurance", 0.87),
        "plan_type_id": _field("Comprehensive", 0.84),
        "product_id": _field("CV", 0.61, verify=True),
        "business_type": _field("ROLLOVER", 0.8),
        "cust_name": _field("SHREE BALAJI LOGISTICS PVT LTD", 0.9),
        "mobileNo": _field("9000456789", 0.88),
        "cust_address": _field("PLOT 14, MIDC PHASE II, NASHIK", 0.69),
        "pinCode": _field("422007", 0.82),
        "reg_num": _field("MH15GH2210", 0.78),
        "search_text_make": _field("TATA", 0.92),
        "model_name": _field("407 GOLD SFC", 0.64, verify=True),
        "VehicleClass": _field("GCV", 0.7),
        "VehicleSubClass": _field("TRUCK", 0.52, verify=True),
        "engineNo": _field("497TC81234567", 0.58, verify=True),
        "chassisNo": _field("MAT448092P1A12345", 0.6, verify=True),
        "vehicle_weight_value": _field("4995", 0.83),
        "no_of_wheels": _field("4", 0.9),
        "rto_num": _field("MH15", 0.85),
        "rto_city": _field("NASHIK", 0.84),
        "rto_state": _field("MAHARASHTRA", 0.86),
        "sum_insured": _field("785000", 0.85),
        "premium": _field("34210", 0.9),
        "tp_premium": _field("19560", 0.88),
        "policy_start_date": _field("20/05/2026", 0.9),
        "policy_end_date": _field("19/05/2027", 0.9),
    }
    cv_doc = [
        "Policy No: CV/2026/GCV/00912    Insurer: Cholamandalam MS",
        "Insured: Shree Balaji Logistics Pvt Ltd",
        "Mobile: 90004 56789    Address: Plot 14, MIDC Phase II, Nashik",
        "Vehicle: Tata 407 Gold SFC  (goods carrier)",
        "Registration No: MH15 GH 2210",
        "Engine: 497TC81234567   Chassis: MAT448092P1A12345",
        "GVW: 4995 kg    Wheels: 4",
        "Cover: Comprehensive (Rollover)   IDV: Rs. 7,85,000",
        "Total Premium: Rs. 34,210",
        "Period: 20/05/2026 to 19/05/2027",
    ]

    # 4) Approved — same shape as the car, reviewer corrected one field.
    approved_fields = dict(car_fields)
    approved_fields["policy_number"] = _field("POL/2026/CAR/7731900", 0.98)
    approved_fields["cust_name"] = _field("VIKRAM MENON", 0.96)
    approved_fields["reg_num"] = _field("TN07CB1199", 0.74, verify=False, edited=True)
    approved_fields["reg_num"]["original_value"] = "TN07C81199"
    approved_fields["email"] = _field("VIKRAM.MENON@EXAMPLE.COM", 0.9)
    approved_fields["cust_address"] = _field("18 BOAT CLUB ROAD, CHENNAI", 0.91)
    approved_doc = [
        "Policy No: POL/2026/CAR/7731900   Insurer: HDFC ERGO",
        "Insured: Vikram Menon   Mobile: 98450 12378",
        "Address: 18 Boat Club Road, Chennai",
        "Registration No: TN07CB1199   (reviewer corrected O/0)",
        "Cover: Comprehensive (Renewal)",
    ]

    # Three more clean straight-through cars (clones of the Creta record with the
    # identity/vehicle fields swapped) so the hero straight-through percentage
    # reads credibly in a demo instead of being dominated by the review rows.
    def _car_variant(overrides):
        fields = dict(car_fields)
        for key, (value, conf) in overrides.items():
            fields[key] = _field(value, conf)
        return fields

    nexon_fields = _car_variant(
        {
            "policy_number": ("POL/2026/CAR/9915532", 0.98),
            "insurer_id": ("ICICI Lombard General Insurance", 0.96),
            "cust_name": ("MEERA PILLAI", 0.95),
            "mobileNo": ("9731204455", 0.94),
            "email": ("MEERA.P@EXAMPLE.COM", 0.92),
            "cust_address": ("221 INDIRANAGAR 100 FT ROAD, BENGALURU", 0.91),
            "reg_num": ("KA05NX2210", 0.96),
            "search_text_make": ("TATA", 0.97),
            "model_name": ("NEXON", 0.95),
            "variantName": ("XZ+ 1.2 PETROL", 0.9),
            "rto_num": ("KA05", 0.96),
            "rto_city": ("BENGALURU SOUTH", 0.94),
            "sum_insured": ("910000", 0.95),
            "premium": ("23140", 0.96),
        }
    )
    swift_fields = _car_variant(
        {
            "policy_number": ("POL/2026/CAR/8821407", 0.98),
            "insurer_id": ("Bajaj Allianz General Insurance", 0.96),
            "cust_name": ("ARJUN NAIR", 0.95),
            "mobileNo": ("9867031122", 0.94),
            "email": ("ARJUN.NAIR@EXAMPLE.COM", 0.92),
            "cust_address": ("12 CARTER ROAD, BANDRA WEST, MUMBAI", 0.91),
            "pinCode": ("400050", 0.95),
            "reg_num": ("MH02SW8894", 0.96),
            "search_text_make": ("MARUTI SUZUKI", 0.97),
            "model_name": ("SWIFT", 0.95),
            "variantName": ("ZXI 1.2", 0.9),
            "rto_num": ("MH02", 0.96),
            "rto_city": ("MUMBAI WEST", 0.94),
            "rto_state": ("MAHARASHTRA", 0.97),
            "sum_insured": ("645000", 0.95),
            "premium": ("17890", 0.96),
        }
    )
    city_fields = _car_variant(
        {
            "policy_number": ("POL/2026/CAR/7730918", 0.98),
            "insurer_id": ("Go Digit General Insurance", 0.96),
            "cust_name": ("SNEHA KULKARNI", 0.95),
            "mobileNo": ("9810276634", 0.94),
            "email": ("SNEHA.K@EXAMPLE.COM", 0.92),
            "cust_address": ("B-14 DEFENCE COLONY, NEW DELHI", 0.91),
            "pinCode": ("110024", 0.95),
            "reg_num": ("DL01CH5521", 0.96),
            "search_text_make": ("HONDA", 0.97),
            "model_name": ("CITY", 0.95),
            "variantName": ("VX CVT", 0.9),
            "rto_num": ("DL01", 0.96),
            "rto_city": ("NEW DELHI", 0.94),
            "rto_state": ("DELHI", 0.97),
            "sum_insured": ("1032000", 0.95),
            "premium": ("26410", 0.96),
        }
    )

    def _car_doc(insurer, name, reg, vehicle, idv, premium):
        return [
            f"Insurer: {insurer}",
            f"Insured: {name}",
            f"Vehicle: {vehicle}",
            f"Registration No: {reg}",
            f"Cover: Comprehensive      IDV: Rs. {idv}",
            f"Total Premium: Rs. {premium}",
            "Period: 15/03/2026 to 14/03/2027",
        ]

    return [
        dict(
            filename="hdfc_creta_comprehensive.pdf",
            form_num="DEMO-CAR-001",
            title="HDFC ERGO — Private Car Package Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.AUTO_APPROVED,
            fields=car_fields,
            pii=_pii(
                6,
                {
                    "name": 1,
                    "mobile": 1,
                    "email": 1,
                    "pincode": 1,
                    "reg": 1,
                    "chassis": 1,
                },
            ),
            doc=car_doc,
            reviewed_age=None,
        ),
        dict(
            filename="icici_activa_twowheeler.pdf",
            form_num="DEMO-TW-002",
            title="ICICI Lombard — Two Wheeler Package Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.NEEDS_REVIEW,
            fields=tw_fields,
            pii=_pii(
                7,
                {
                    "name": 1,
                    "mobile": 1,
                    "email": 1,
                    "pincode": 1,
                    "reg": 1,
                    "engine": 1,
                    "chassis": 1,
                },
            ),
            doc=tw_doc,
            reviewed_age=None,
        ),
        dict(
            filename="chola_tata407_gcv.pdf",
            form_num="DEMO-CV-003",
            title="Cholamandalam MS — Commercial Vehicle Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.NEEDS_REVIEW,
            fields=cv_fields,
            pii=_pii(
                5, {"name": 1, "mobile": 1, "pincode": 1, "engine": 1, "chassis": 1}
            ),
            doc=cv_doc,
            reviewed_age=None,
        ),
        dict(
            filename="hdfc_menon_approved.pdf",
            form_num="DEMO-CAR-004",
            title="HDFC ERGO — Private Car Package Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.APPROVED,
            fields=approved_fields,
            pii=_pii(
                6,
                {
                    "name": 1,
                    "mobile": 1,
                    "email": 1,
                    "pincode": 1,
                    "reg": 1,
                    "chassis": 1,
                },
            ),
            doc=approved_doc,
            reviewed_age=timedelta(hours=3),
        ),
        dict(
            filename="icici_nexon_comprehensive.pdf",
            form_num="DEMO-CAR-005",
            title="ICICI Lombard — Private Car Package Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.AUTO_APPROVED,
            fields=nexon_fields,
            pii=_pii(
                6,
                {
                    "name": 1,
                    "mobile": 1,
                    "email": 1,
                    "pincode": 1,
                    "reg": 1,
                    "chassis": 1,
                },
            ),
            doc=_car_doc(
                "ICICI Lombard",
                "Meera Pillai",
                "KA05NX2210",
                "Tata Nexon XZ+",
                "9,10,000",
                "23,140",
            ),
            reviewed_age=None,
        ),
        dict(
            filename="bajaj_swift_comprehensive.pdf",
            form_num="DEMO-CAR-006",
            title="Bajaj Allianz — Private Car Package Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.AUTO_APPROVED,
            fields=swift_fields,
            pii=_pii(
                6,
                {
                    "name": 1,
                    "mobile": 1,
                    "email": 1,
                    "pincode": 1,
                    "reg": 1,
                    "chassis": 1,
                },
            ),
            doc=_car_doc(
                "Bajaj Allianz",
                "Arjun Nair",
                "MH02SW8894",
                "Maruti Suzuki Swift ZXi",
                "6,45,000",
                "17,890",
            ),
            reviewed_age=None,
        ),
        dict(
            filename="godigit_city_comprehensive.pdf",
            form_num="DEMO-CAR-007",
            title="Go Digit — Private Car Package Policy",
            review_status=PolicyExtractionRequest.ReviewStatus.AUTO_APPROVED,
            fields=city_fields,
            pii=_pii(
                6,
                {
                    "name": 1,
                    "mobile": 1,
                    "email": 1,
                    "pincode": 1,
                    "reg": 1,
                    "chassis": 1,
                },
            ),
            doc=_car_doc(
                "Go Digit",
                "Sneha Kulkarni",
                "DL01CH5521",
                "Honda City VX CVT",
                "10,32,000",
                "26,410",
            ),
            reviewed_age=None,
        ),
    ]


class Command(BaseCommand):
    help = "Seed completed Policy Extract demo rows (synthetic, no external calls)."

    def handle(self, *args, **options):
        existing = PolicyExtractionRequest.objects.filter(
            **{f"metadata__{DEMO_MARKER}": True}
        )
        deleted = existing.count()
        existing.delete()
        if deleted:
            self.stdout.write(f"Removed {deleted} previous demo row(s).")

        now = timezone.now()
        created = 0
        for i, rec in enumerate(_records()):
            request_id = uuid.uuid4()
            storage_key = f"policy_extractions/originals/{request_id}/{rec['filename']}"
            pdf_bytes = _synthetic_pdf(rec["title"], rec["doc"])
            default_storage.save(storage_key, ContentFile(pdf_bytes))

            result = {
                "pdf_path": rec["filename"],
                "model_used": "demo-seed",
                "extraction_timestamp": now.isoformat(),
                "processing_time_seconds": 18.4,
                "extracted_fields": rec["fields"],
                "pipeline": "minerva-api-two-stage",
                "pii_masking": rec["pii"],
                "cost_summary": {
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                    "model": "demo-seed",
                },
            }
            reviewed_at = None
            if rec["reviewed_age"] is not None:
                reviewed_at = now - rec["reviewed_age"]

            PolicyExtractionRequest.objects.create(
                original_filename=rec["filename"],
                source_url=storage_key,
                status=PolicyExtractionRequest.Status.COMPLETED,
                review_status=rec["review_status"],
                reviewed_at=reviewed_at,
                result=result,
                metadata={DEMO_MARKER: True, "form_num": rec["form_num"]},
            )
            created += 1

        by_status: dict[str, int] = {}
        for rec in _records():
            by_status[rec["review_status"]] = by_status.get(rec["review_status"], 0) + 1
        breakdown = ", ".join(f"{n} {s}" for s, n in sorted(by_status.items()))
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created} demo policy extraction(s): {breakdown}."
            )
        )
