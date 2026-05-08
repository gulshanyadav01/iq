#!/usr/bin/env python3
"""
Email (.eml) Content Extractor
Extracts text content from .eml email files for commission data extraction.
Includes support for extracting and processing attachments.
"""

import email
import sys
import os
import tempfile
from pathlib import Path
from email.policy import default

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

from .extract_pdf import extract_pdf_text
from .convert_xlsb import convert_xlsb_to_xlsx, XlsbConversionError

# Import existing extraction modules
PDF_EXTRACTOR_AVAILABLE = False
EXCEL_EXTRACTOR_AVAILABLE = True


def html_table_to_markdown(html_content):
    """
    Convert HTML tables to markdown format for better preservation.

    Args:
        html_content: HTML content string

    Returns:
        str: Content with tables converted to markdown
    """
    if not BS4_AVAILABLE:
        # Fallback: return HTML as-is if BeautifulSoup not available
        return html_content

    try:
        from bs4 import BeautifulSoup as BS
        soup = BS(html_content, 'html.parser')

        # Find all tables
        tables = soup.find_all('table')

        if not tables:
            # No tables, just extract text
            return soup.get_text(separator='\n', strip=True)

        # Replace each table with markdown version
        for table in tables:
            md_table = _convert_html_table_to_markdown(table)
            # Replace table with markdown version
            table.replace_with(BS(md_table, 'html.parser'))

        # Get final text with markdown tables
        return soup.get_text(separator='\n', strip=True)

    except Exception:
        # If parsing fails, return original content
        return html_content


def _convert_html_table_to_markdown(table_element):
    """
    Convert a single HTML table element to markdown table format.
    Handles complex headers with rowspan/colspan by creating combined header labels.

    Args:
        table_element: BeautifulSoup table element

    Returns:
        str: Markdown formatted table
    """
    html_rows = table_element.find_all('tr')

    if not html_rows:
        return "[Empty table]"

    # Check if first row has rowspan/colspan (complex header)
    first_row_cells = html_rows[0].find_all(['th', 'td'])
    has_complex_header = any(
        cell.get('rowspan', '1') != '1' or cell.get('colspan', '1') != '1'
        for cell in first_row_cells
    )

    # If complex header with 2 rows, combine them
    if has_complex_header and len(html_rows) >= 2:
        # Parse multi-row header
        header_labels = _parse_multirow_header(html_rows[:2])
        data_rows = html_rows[2:]  # Skip header rows

        # Build markdown
        rows = []
        # Header row
        md_header = '| ' + ' | '.join(header_labels) + ' |'
        rows.append(md_header)
        # Separator
        separator = '| ' + ' | '.join(['---'] * len(header_labels)) + ' |'
        rows.append(separator)

        # Data rows
        for row in data_rows:
            cells = row.find_all(['th', 'td'])
            cell_texts = [cell.get_text(strip=True).replace('|', '\\|') for cell in cells]
            if cell_texts:
                md_row = '| ' + ' | '.join(cell_texts) + ' |'
                rows.append(md_row)

        return '\n' + '\n'.join(rows) + '\n'

    else:
        # Simple table - just convert rows as-is
        rows = []
        for row_idx, row in enumerate(html_rows):
            cells = row.find_all(['th', 'td'])
            cell_texts = [cell.get_text(strip=True).replace('|', '\\|') for cell in cells]

            if cell_texts:
                md_row = '| ' + ' | '.join(cell_texts) + ' |'
                rows.append(md_row)

                # Add separator after first row
                if row_idx == 0:
                    separator = '| ' + ' | '.join(['---'] * len(cell_texts)) + ' |'
                    rows.append(separator)

        return '\n' + '\n'.join(rows) + '\n'


def _parse_multirow_header(header_rows):
    """
    Parse a multi-row header with rowspan/colspan and create flat column labels.

    Args:
        header_rows: List of header row elements (typically 2 rows)

    Returns:
        List of combined header labels for each column
    """
    # Parse row 1 (parent headers with colspan)
    row1_cells = header_rows[0].find_all(['th', 'td'])
    row2_cells = header_rows[1].find_all(['th', 'td'])

    labels = []
    row2_idx = 0

    for cell in row1_cells:
        text = cell.get_text(strip=True)
        colspan = int(cell.get('colspan', '1'))
        rowspan = int(cell.get('rowspan', '1'))

        if rowspan > 1:
            # This cell spans both rows - use it as-is
            labels.append(text)
        else:
            # This cell has child cells in row 2
            for _ in range(colspan):
                if row2_idx < len(row2_cells):
                    child_text = row2_cells[row2_idx].get_text(strip=True)
                    # Combine parent and child: "Parent - Child"
                    if text and child_text:
                        combined = f"{text} - {child_text}"
                    else:
                        combined = text or child_text
                    labels.append(combined)
                    row2_idx += 1

    return labels


def process_pdf_attachment(attachment_data, filename="attachment.pdf", output_dir=None):
    """
    Process a PDF attachment and extract its text content using extract_pdf module.

    Args:
        attachment_data: Binary data of the PDF attachment
        filename: Name of the PDF attachment (for saving to output_dir)
        output_dir: Optional directory to save the PDF attachment and extracted text

    Returns:
        str: Extracted text from the PDF
    """
    # Save attachment to output directory if provided, otherwise use temp file
    if output_dir:
        from pathlib import Path

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Save PDF to output directory
        safe_filename = Path(filename).stem.replace(" ", "_")
        pdf_path = output_path / f"attachment_{safe_filename}.pdf"
        pdf_path.write_bytes(attachment_data)

        # Extract text
        text = extract_pdf_text(str(pdf_path))

        # Save extracted text
        text_path = output_path / f"extracted_text_{safe_filename}.txt"
        text_path.write_text(text if text else "[No text extracted]", encoding="utf-8")

        return text if text else "[PDF attachment - no text extracted]"
    else:
        # Use temporary file (fallback for backwards compatibility)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_file.write(attachment_data)
            tmp_path = tmp_file.name

        try:
            # Use the existing extract_pdf module
            text = extract_pdf_text(tmp_path)
            return text if text else "[PDF attachment - no text extracted]"

        except Exception as e:
            return f"[PDF attachment - error extracting: {str(e)}]"

        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def process_excel_attachment(attachment_data, filename, api_key=None):
    """
    Save Excel attachment to temp file for later sheet-by-sheet processing.

    Args:
        attachment_data: Binary data of the Excel attachment
        filename: Name of the attachment file
        api_key: Optional Gemini API key (unused, kept for compatibility)

    Returns:
        dict: {
            "temp_path": str,  # Path to temp file (not deleted, for later processing)
            "filename": str,
            "content_type": str,
            "size": int
        }
        Or dict with "error" key if extraction unavailable
    """
    # Save attachment to temporary file (DON'T delete - will be used later)
    suffix = Path(filename).suffix.lower()
    if suffix not in [".xlsx", ".xls", ".xlsm", ".xlsb"]:
        suffix = ".xlsx"  # default

    tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_file.write(attachment_data)
    tmp_file.close()

    # .xlsb (Excel binary) is not readable by openpyxl. Convert to .xlsx via
    # LibreOffice so the rest of the pipeline can treat it like any other workbook.
    if suffix == ".xlsb":
        try:
            converted_path = convert_xlsb_to_xlsx(tmp_file.name)
        except XlsbConversionError as e:
            try:
                os.unlink(tmp_file.name)
            except Exception:
                pass
            return {
                "filename": filename,
                "content_type": "application/vnd.ms-excel.sheet.binary.macroenabled.12",
                "size": len(attachment_data),
                "error": f"xlsb conversion failed: {e}",
            }

        try:
            os.unlink(tmp_file.name)
        except Exception:
            pass

        converted_filename = Path(filename).with_suffix(".xlsx").name
        return {
            "temp_path": converted_path,
            "filename": converted_filename,
            "original_filename": filename,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size": Path(converted_path).stat().st_size,
            "converted_from": "xlsb",
        }

    return {
        "temp_path": tmp_file.name,
        "filename": filename,
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "size": len(attachment_data),
    }


def _image_is_likely_table(width: int, height: int, file_size_bytes: int) -> bool:
    """
    Heuristic gate before sending an image to a vision LLM. Returns True only
    if the image's geometry looks like a screenshot of a commission grid.

    Drops:
      - signature/logo icons (small dimensions or tiny file size)
      - separator gifs (extreme aspect ratios)
      - decorative thumbnails (low total pixel count)
      - oversized animations / banner ads (real grid screenshots are
        ~50–500 KB; multi-MB files are almost always animated gifs)
    """
    if width < 500 or height < 200:
        return False
    if width * height < 150_000:
        return False
    aspect = width / height if height else 0
    if not (0.3 <= aspect <= 6.0):
        return False
    if file_size_bytes < 8 * 1024:
        return False
    if file_size_bytes > 2 * 1024 * 1024:
        return False
    return True


def process_image_attachment(
    attachment_data, filename, output_dir=None, content_type=""
):
    """
    Save an image attachment and decide whether it's plausibly a commission
    table. Vision-LLM extraction is driven by the indexer (not here) so this
    function stays pure and re-usable.

    Returns a dict with:
      filename, content_type, size, image_path, is_likely_table, width, height
    """
    from pathlib import Path
    try:
        from PIL import Image
    except ImportError:
        return {
            "filename": filename,
            "content_type": content_type,
            "size": len(attachment_data),
            "image_path": "",
            "is_likely_table": False,
            "width": 0,
            "height": 0,
            "error": "Pillow not installed; cannot inspect image",
        }

    safe_filename = Path(filename).name.replace(" ", "_") or "attachment"
    if output_dir:
        attachments_dir = Path(output_dir) / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        image_path = attachments_dir / safe_filename
        image_path.write_bytes(attachment_data)
    else:
        suffix = Path(filename).suffix or ".bin"
        tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_file.write(attachment_data)
        tmp_file.close()
        image_path = Path(tmp_file.name)

    width = height = 0
    try:
        with Image.open(image_path) as im:
            width, height = im.size
    except Exception:
        pass

    return {
        "filename": filename,
        "content_type": content_type,
        "size": len(attachment_data),
        "image_path": str(image_path),
        "is_likely_table": _image_is_likely_table(width, height, len(attachment_data)),
        "width": width,
        "height": height,
    }


def extract_cid_refs_from_body(body_text_or_html: str) -> set:
    """
    Return the set of cid: identifiers referenced from a body string.

    Matches both the raw HTML form (`src="cid:foo@hash"`) and the textual form
    that html2text-style converters leave behind (`[cid:foo@hash]`). Used by
    the indexer to keep image-attachment processing scoped to images that
    actually appear in the *latest* email — older quoted messages reference
    images that should be ignored even if their MIME parts still exist.
    """
    import re
    if not body_text_or_html:
        return set()
    return set(re.findall(r"cid:([^\s\"'>\)\]]+)", body_text_or_html))


def extract_eml_content(eml_path, output_dir=None):
    """
    Extract content from an .eml email file, including attachments.

    Args:
        eml_path: Path to the .eml file
        output_dir: Optional directory to save attachments and extracted text

    Returns:
        dict with 'subject', 'from', 'date', 'body', 'attachments' keys
    """
    try:
        with open(eml_path, "r", encoding="utf-8", errors="ignore") as f:
            msg = email.message_from_file(f, policy=default)

        # Extract metadata
        subject = msg.get("Subject", "")
        from_addr = msg.get("From", "")
        to_addr = msg.get("To", "")
        cc_addr = msg.get("Cc", "")
        date = msg.get("Date", "")

        # Extract body content
        body = ""
        attachments = []

        if msg.is_multipart():
            # Handle multipart messages (prefer text/plain over text/html)
            text_parts = []
            html_parts = []

            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # Check if this part is an attachment
                if "attachment" in content_disposition or part.get_filename():
                    filename = part.get_filename()
                    if filename:
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                attachment_info = {
                                    "filename": filename,
                                    "content_type": content_type,
                                    "size": len(payload),
                                }

                                # Process based on file type
                                if (
                                    content_type == "application/pdf"
                                    or filename.lower().endswith(".pdf")
                                ):
                                    attachment_info["extracted_content"] = (
                                        process_pdf_attachment(
                                            payload,
                                            filename=filename,
                                            output_dir=output_dir,
                                        )
                                    )
                                elif content_type in [
                                    "application/vnd.ms-excel",
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    "application/vnd.ms-excel.sheet.binary.macroenabled.12",
                                    "application/vnd.ms-excel.sheet.macroenabled.12",
                                ] or filename.lower().endswith((".xls", ".xlsx", ".xlsm", ".xlsb")):
                                    # For Excel, save temp path for later sheet-by-sheet processing
                                    excel_info = process_excel_attachment(
                                        payload, filename
                                    )
                                    attachment_info.update(excel_info)
                                elif content_type.startswith("image/") or filename.lower().endswith(
                                    (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
                                ):
                                    image_info = process_image_attachment(
                                        payload,
                                        filename,
                                        output_dir=output_dir,
                                        content_type=content_type,
                                    )
                                    attachment_info.update(image_info)
                                    cid = part.get("Content-ID", "").strip("<>")
                                    if cid:
                                        attachment_info["content_id"] = cid
                                else:
                                    # Try to extract as text if it's a text file
                                    if content_type.startswith("text/"):
                                        try:
                                            attachment_info["extracted_content"] = (
                                                payload.decode("utf-8", errors="ignore")
                                            )
                                        except Exception:
                                            attachment_info["extracted_content"] = (
                                                f"[{filename} - binary attachment, cannot extract text]"
                                            )
                                    else:
                                        attachment_info["extracted_content"] = (
                                            f"[{filename} - binary attachment, cannot extract text]"
                                        )

                                attachments.append(attachment_info)
                        except Exception as e:
                            attachments.append(
                                {
                                    "filename": filename,
                                    "error": f"Error processing attachment: {str(e)}",
                                }
                            )

                # Extract body text
                elif content_type == "text/plain":
                    try:
                        text_parts.append(part.get_content())
                    except Exception:
                        # Fallback to payload decode
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                text_parts.append(
                                    payload.decode("utf-8", errors="ignore")
                                )
                        except Exception:
                            pass

                elif content_type == "text/html":
                    try:
                        html_parts.append(part.get_content())
                    except Exception:
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                html_parts.append(
                                    payload.decode("utf-8", errors="ignore")
                                )
                        except Exception:
                            pass

            # Prefer HTML if it contains tables, otherwise prefer plain text
            has_html_tables = any('<table' in part.lower() for part in html_parts)

            if has_html_tables and html_parts:
                # Convert HTML tables to markdown for better preservation
                converted_parts = []
                for html_part in html_parts:
                    converted = html_table_to_markdown(html_part)
                    converted_parts.append(converted)
                body = "\n\n".join(converted_parts)
            elif text_parts:
                body = "\n\n".join(text_parts)
            elif html_parts:
                # Fallback to HTML without tables
                converted_parts = []
                for html_part in html_parts:
                    converted = html_table_to_markdown(html_part)
                    converted_parts.append(converted)
                body = "\n\n".join(converted_parts)

        else:
            # Single-part message
            try:
                body = msg.get_content()
            except Exception:
                try:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="ignore")
                    else:
                        body = msg.get_payload()
                except Exception as e:
                    body = f"Error extracting body: {str(e)}"

        return {
            "subject": subject,
            "from": from_addr,
            "to": to_addr,
            "cc": cc_addr,
            "date": date,
            "body": body,
            "attachments": attachments,
            # Raw HTML parts retained so the indexer can grep for `cid:` image
            # references that html_table_to_markdown drops when extracting text.
            "raw_html_parts": html_parts if msg.is_multipart() else [],
        }

    except Exception as e:
        return {
            "subject": "",
            "from": "",
            "to": "",
            "cc": "",
            "date": "",
            "body": f"Error processing email: {str(e)}",
            "attachments": [],
        }


def main():
    """CLI entry point"""
    if len(sys.argv) < 2:
        print("Usage: python extract_eml.py <path_to_eml_file>")
        sys.exit(1)

    eml_path = sys.argv[1]

    if not Path(eml_path).exists():
        print(f"Error: File not found: {eml_path}")
        sys.exit(1)

    # Debug: Print module availability to stderr
    print(f"# PDF extraction available: {PDF_EXTRACTOR_AVAILABLE}", file=sys.stderr)
    print(f"# Excel extraction available: {EXCEL_EXTRACTOR_AVAILABLE}", file=sys.stderr)

    result = extract_eml_content(eml_path)

    # Debug: Print attachment count to stderr
    print(f"# Attachments found: {len(result.get('attachments', []))}", file=sys.stderr)

    # Print formatted output
    print(f"Subject: {result['subject']}")
    print(f"From: {result['from']}")
    print(f"Date: {result['date']}")
    print("=" * 70)
    print(result["body"])

    # Print attachments if any
    if result.get("attachments"):
        print("\n" + "=" * 70)
        print(f"ATTACHMENTS ({len(result['attachments'])} found)")
        print("=" * 70)

        for i, attachment in enumerate(result["attachments"], 1):
            print(f"\n--- Attachment {i}: {attachment.get('filename', 'Unknown')} ---")

            if "error" in attachment:
                print(f"Error: {attachment['error']}")
            else:
                print(f"Type: {attachment.get('content_type', 'Unknown')}")
                print(f"Size: {attachment.get('size', 0)} bytes")

                if "extracted_content" in attachment:
                    print("\nExtracted Content:")
                    print("-" * 70)
                    print(attachment["extracted_content"])


if __name__ == "__main__":
    main()
