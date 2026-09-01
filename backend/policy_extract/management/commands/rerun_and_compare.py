"""Locally re-run policy extraction over a QA sheet and diff a field vs Permute.

Debugging tool for the "wrong Carrier" investigation. Given a sheet exported from
the Permute-vs-Rhobots QA workbook (a ticket-id column + a Permute expected-value
column), for each row it:

  1. Fetches the source PDF from the *production* HTTP API (the policy-extract
     endpoints are AllowAny), by looking the ticket up via ``?search=`` and
     streaming ``/document/`` to a local folder.
  2. Re-extracts locally with ``MineruGeminiParser`` (uses the local master-data
     DB + GOOGLE_API_KEY/MINERVA_API_URL), writing ``.md``/``_raw.json``
     intermediates so a mismatch can be traced to a pipeline stage.
  3. Diffs the fresh value of a field (default ``vechicle_carrier_id``) against the
     Permute column and writes a self-diagnosing CSV.

Run inside the local dev backend, e.g.::

    docker compose exec backend python manage.py rerun_and_compare \
        --input /work/rows.csv \
        --id-col "Ticket ID" \
        --permute-col "Carrier (Permute)" \
        --prod-base-url https://<prod-host> \
        --pdf-dir /tmp/pe_pdfs \
        --intermediate-dir /tmp/pe_debug \
        --out /tmp/carrier_cmp.csv

Nothing here writes to the database; production is only read from.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.core.management.base import BaseCommand, CommandError

# The field whose value we diff by default. "Carrier" in the QA sheet maps to the
# vechicle_carrier_id enum (PUBLIC/PRIVATE/COMMERCIAL); see mineru_gemini_parser.
DEFAULT_FIELD = "vechicle_carrier_id"

# Signals that drive the carrier derivation in _post_process — surfaced per row so
# the CSV alone points at which stage produced a wrong value.
SIGNAL_FIELDS = ("VehicleClass", "product_id")

REQUEST_TIMEOUT = 60


class Command(BaseCommand):
    help = (
        "Fetch production PDFs by ticket id, re-run extraction locally, and diff a "
        "field (default vechicle_carrier_id) against a Permute column from a QA sheet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            required=True,
            help="Path to the QA sheet exported as .csv or .xlsx.",
        )
        parser.add_argument(
            "--prod-base-url",
            required=True,
            help="Base URL of the production API, e.g. https://host (no trailing /api).",
        )
        parser.add_argument(
            "--id-col",
            default="Ticket ID",
            help="Header of the column holding the PBP ticket id (default: 'Ticket ID').",
        )
        parser.add_argument(
            "--permute-col",
            default="Carrier (Permute)",
            help="Header of the Permute expected-value column to diff against.",
        )
        parser.add_argument(
            "--field",
            default=DEFAULT_FIELD,
            help=f"extracted_fields key to compare (default: {DEFAULT_FIELD}).",
        )
        parser.add_argument(
            "--pdf-dir",
            default="/tmp/pe_pdfs",
            help="Local directory to store downloaded source PDFs.",
        )
        parser.add_argument(
            "--intermediate-dir",
            default="/tmp/pe_debug",
            help="Local directory for per-ticket .md/_raw.json intermediates.",
        )
        parser.add_argument(
            "--out",
            default="/tmp/carrier_cmp.csv",
            help="Path to write the comparison CSV.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Only process the first N rows (0 = all). Handy for a smoke test.",
        )
        parser.add_argument(
            "--skip-download",
            action="store_true",
            help="Reuse PDFs already present in --pdf-dir; do not hit the prod API for them.",
        )

    # ── entrypoint ────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        self._preflight()

        rows = self._read_sheet(opts["input"], opts["id_col"], opts["permute_col"])
        if opts["limit"]:
            rows = rows[: opts["limit"]]
        if not rows:
            raise CommandError("No rows found in the input sheet.")

        base = opts["prod_base_url"].rstrip("/")
        field = opts["field"]
        pdf_dir = Path(opts["pdf_dir"])
        int_dir = Path(opts["intermediate_dir"])
        pdf_dir.mkdir(parents=True, exist_ok=True)
        int_dir.mkdir(parents=True, exist_ok=True)

        # Import lazily so --help / preflight failures don't pay the model import cost.
        from policy_extract.services.mineru_gemini_parser import MineruGeminiParser

        out_rows: List[Dict[str, Any]] = []
        matches = mismatches = errors = 0

        for i, (ticket, permute) in enumerate(rows, start=1):
            self.stdout.write(f"[{i}/{len(rows)}] {ticket} …")
            rec: Dict[str, Any] = {
                "ticket": ticket,
                "permute": permute,
                "old_rhobots": "",
                "new_rhobots": "",
                "match": "",
                "vehicle_class": "",
                "product": "",
                "raw_carrier": "",
                "resolver_matched": "",
                "pdf_path": "",
                "raw_json_path": "",
                "error": "",
            }
            try:
                uuid, old_val, orig_name = self._lookup(base, ticket, field)
                pdf_path = pdf_dir / self._pdf_filename(ticket, orig_name)
                if not (opts["skip_download"] and pdf_path.exists()):
                    self._download(base, uuid, pdf_path)
                rec["old_rhobots"] = old_val
                rec["pdf_path"] = str(pdf_path)

                ticket_int_dir = int_dir / self._safe(ticket)
                parser = MineruGeminiParser(
                    api_key=os.getenv("GOOGLE_API_KEY"),
                    intermediate_dir=str(ticket_int_dir),
                )
                result = parser.parse_pdf(str(pdf_path), save_intermediate=True)
                fields = (result or {}).get("extracted_fields") or {}

                new_val = self._field_value(fields, field)
                rec["new_rhobots"] = new_val
                rec["vehicle_class"] = self._field_value(fields, "VehicleClass")
                rec["product"] = self._field_value(fields, "product_id")
                rec["resolver_matched"] = str(bool((fields.get(field) or {}).get("id")))

                raw_json = self._find_raw_json(ticket_int_dir, pdf_path)
                if raw_json:
                    rec["raw_json_path"] = str(raw_json)
                    rec["raw_carrier"] = self._raw_carrier_hint(raw_json)

                is_match = self._norm(new_val) == self._norm(permute)
                rec["match"] = "TRUE" if is_match else "FALSE"
                matches += is_match
                mismatches += not is_match
            except Exception as exc:  # noqa: BLE001 — per-row isolation, keep going
                rec["error"] = str(exc)
                errors += 1
                self.stderr.write(self.style.WARNING(f"    ✗ {exc}"))

            out_rows.append(rec)

        self._write_csv(opts["out"], out_rows)
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{len(rows)} rows · {matches} match · {mismatches} mismatch · "
                f"{errors} error → {opts['out']}"
            )
        )

    # ── preflight ─────────────────────────────────────────────────────────────
    def _preflight(self):
        from master_data.models import CarrierMaster

        if not CarrierMaster.objects.exists():
            raise CommandError(
                "CarrierMaster is empty — master data is not seeded. Run "
                "`python manage.py load_master_data` first (carrier resolution "
                "needs it)."
            )
        vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not vertex and not os.getenv("GOOGLE_API_KEY"):
            raise CommandError(
                "GOOGLE_API_KEY is not set (and Vertex mode is off) — the parser "
                "cannot call Gemini."
            )
        if not os.getenv("MINERVA_API_URL"):
            self.stdout.write(
                self.style.WARNING(
                    "MINERVA_API_URL not set — the parser will use its built-in "
                    "default endpoint."
                )
            )

    # ── prod API ──────────────────────────────────────────────────────────────
    def _lookup(self, base: str, ticket: str, field: str) -> Tuple[str, str, str]:
        """Resolve a ticket to (uuid, old field value, original_filename).

        Uses the fuzzy ``?search=`` list endpoint and pins the row whose
        ``original_filename`` matches the ticket exactly; paginates until found.
        """
        url: Optional[str] = (
            f"{base}/api/policy-extract/requests/?search={requests.utils.quote(ticket)}"
        )
        candidates: List[dict] = []
        while url:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get(
                "results", payload if isinstance(payload, list) else []
            )
            for item in results:
                if (item.get("original_filename") or "").strip() == ticket.strip():
                    candidates.append(item)
            url = payload.get("next") if isinstance(payload, dict) else None

        if not candidates:
            raise LookupError(
                f"no production request with original_filename == {ticket!r}"
            )
        if len(candidates) > 1:
            raise LookupError(
                f"{len(candidates)} production requests match {ticket!r} exactly — ambiguous"
            )
        item = candidates[0]
        return (
            item["id"],
            self._field_value(item.get("result") or {}, field, nested=True),
            (item.get("original_filename") or ticket),
        )

    def _download(self, base: str, uuid: str, dest: Path):
        url = f"{base}/api/policy-extract/requests/{uuid}/document/"
        with requests.get(url, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            if resp.status_code == 404:
                raise FileNotFoundError("prod has no stored document for this request")
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _field_value(container: dict, key: str, nested: bool = False) -> str:
        """Read ``bucket.value`` for a field. ``nested`` digs through the stored
        request's ``result['extracted_fields']`` shape."""
        src = (container.get("extracted_fields") or {}) if nested else container
        bucket = src.get(key)
        if isinstance(bucket, dict):
            return (bucket.get("value") or "").strip()
        return ""

    @staticmethod
    def _pdf_filename(ticket: str, orig_name: str) -> str:
        ext = os.path.splitext(orig_name or "")[1]
        if not ext:
            ext = ".pdf"  # file_link uploads are named after the bare PBP id, no ext
        return f"{Command._safe(ticket)}{ext}"

    @staticmethod
    def _safe(name: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)

    @staticmethod
    def _find_raw_json(int_dir: Path, pdf_path: Path) -> Optional[Path]:
        expected = int_dir / f"{pdf_path.stem}_raw.json"
        if expected.exists():
            return expected
        hits = sorted(int_dir.glob("*_raw.json")) if int_dir.exists() else []
        return hits[0] if hits else None

    @staticmethod
    def _raw_carrier_hint(raw_json: Path) -> str:
        """Best-effort: pull any label/value in the Stage-1 raw JSON mentioning
        'carrier', without assuming the raw schema. Truncated for the CSV."""
        try:
            data = json.loads(raw_json.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return ""
        hits: List[str] = []

        def walk(node: Any):
            if isinstance(node, dict):
                for k, v in node.items():
                    kl = str(k).lower()
                    if ("carrier" in kl or "permit" in kl) and not isinstance(
                        v, (dict, list)
                    ):
                        hits.append(f"{k}={v}")
                    walk(v)
            elif isinstance(node, list):
                for it in node:
                    walk(it)
            elif isinstance(node, str) and "carrier" in node.lower():
                hits.append(node)

        walk(data)
        return " | ".join(dict.fromkeys(hits))[:200]

    @staticmethod
    def _norm(value: str) -> str:
        return (value or "").strip().casefold()

    # ── sheet IO ──────────────────────────────────────────────────────────────
    def _read_sheet(
        self, path: str, id_col: str, permute_col: str
    ) -> List[Tuple[str, str]]:
        p = Path(path)
        if not p.exists():
            raise CommandError(f"input not found: {path}")
        if p.suffix.lower() == ".xlsx":
            records = self._read_xlsx(p)
        elif p.suffix.lower() == ".csv":
            with open(p, newline="", encoding="utf-8-sig") as f:
                records = list(csv.DictReader(f))
        else:
            raise CommandError("input must be .csv or .xlsx")

        if records and id_col not in records[0]:
            raise CommandError(
                f"column {id_col!r} not in sheet headers: {list(records[0].keys())}"
            )
        if records and permute_col not in records[0]:
            raise CommandError(
                f"column {permute_col!r} not in sheet headers: {list(records[0].keys())}"
            )
        out: List[Tuple[str, str]] = []
        for r in records:
            ticket = (r.get(id_col) or "").strip()
            if ticket:
                out.append((ticket, (r.get(permute_col) or "").strip()))
        return out

    @staticmethod
    def _read_xlsx(path: Path) -> List[dict]:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = [str(c).strip() if c is not None else "" for c in next(rows)]
        except StopIteration:
            return []
        records = []
        for row in rows:
            records.append(
                {
                    header[i]: ("" if v is None else str(v))
                    for i, v in enumerate(row)
                    if i < len(header)
                }
            )
        wb.close()
        return records

    @staticmethod
    def _write_csv(out_path: str, rows: List[Dict[str, Any]]):
        cols = [
            "ticket",
            "permute",
            "old_rhobots",
            "new_rhobots",
            "match",
            "vehicle_class",
            "product",
            "raw_carrier",
            "resolver_matched",
            "pdf_path",
            "raw_json_path",
            "error",
        ]
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            for r in rows:
                writer.writerow({c: r.get(c, "") for c in cols})
