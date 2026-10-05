"""Real, minimal PDFs for tests that run the pre-MinerU ``precheck_pdf``."""

import io

import pypdfium2 as pdfium


def make_pdf(pages: int = 1) -> bytes:
    """A valid blank PDF with ``pages`` pages."""
    doc = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            doc.new_page(612, 792)
        buf = io.BytesIO()
        doc.save(buf)
    finally:
        doc.close()
    return buf.getvalue()
