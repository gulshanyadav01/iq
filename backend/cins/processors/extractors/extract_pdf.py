#!/usr/bin/env python3
"""
PDF Content Extractor
Extracts text content from PDF files for commission data extraction.
Uses API-based extraction with OCR, table detection, and formula support.
"""

from pathlib import Path
import time
import requests
import logging

from core.services.mineru import (
    MINERU_BACKEND,
    MINERU_TIMEOUT_SECONDS,
    MINERU_URL,
    base_form,
    get_session,
    is_retryable,
    precheck_pdf,
)

# Configure logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

# Waits between attempts on a transient MinerU failure (502 restart, 503 limiter
# full, 504, network). This runs synchronously inside the CINS task, so the retry
# is in-process and bounded rather than a Celery re-queue.
_RETRY_WAITS_SECONDS = (5, 15, 45)


def extract_pdf_content(
    file_path: str,
    api_url: str = MINERU_URL,
    return_md: bool = True,
    return_images: bool = False,
    table_enable: bool = True,
    formula_enable: bool = True,
    force_enable_ocr: bool = True,
    parse_method: str = "auto",
    backend: str = MINERU_BACKEND,
    lang_list: str = "en",
    start_page_id: int = 0,
    end_page_id: int = 99999,
) -> dict:
    """
    Extract content from a PDF file using the file_parse API.

    Args:
        file_path: Path to the PDF file (must be a .pdf file)
        api_url: URL of the file_parse API endpoint
        return_md: Whether to return markdown content
        return_images: Whether to return extracted images
        table_enable: Whether to enable table extraction
        formula_enable: Whether to enable formula extraction
        force_enable_ocr: Whether to force OCR processing
        parse_method: Parsing method to use ('auto', 'ocr', etc.)
        backend: Backend engine to use
        lang_list: Comma-separated list of languages
        start_page_id: Starting page index (0-based)
        end_page_id: Ending page index

    Returns:
        dict: API response containing extracted content

    Raises:
        ValueError: If the file is not a PDF
        FileNotFoundError: If the file does not exist
        PermanentDocumentError: If the PDF is encrypted or corrupt
        requests.RequestException: If the API request fails (after retrying
            transient failures)
    """
    logger.debug(f"Starting PDF extraction for file: {file_path}")
    path = Path(file_path)

    # Validate file is a PDF
    if path.suffix.lower() != ".pdf":
        logger.error(f"Invalid file type: {path.suffix}. Expected .pdf")
        raise ValueError(f"Only PDF files are supported. Got: {path.suffix}")

    # Check file exists
    if not path.exists():
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size = path.stat().st_size
    logger.info(f"Processing PDF: {path.name} (size: {file_size:,} bytes)")

    # Encrypted/corrupt PDFs fail inside MinerU after holding a GPU slot; reject
    # them locally. No page/size cap here — commission grids can run long.
    precheck_pdf(str(path))

    # Prepare form data
    data = base_form(
        return_md=str(return_md).lower(),
        return_images=str(return_images).lower(),
        end_page_id=str(end_page_id),
        parse_method=parse_method,
        start_page_id=str(start_page_id),
        lang_list=lang_list,
        backend=backend,
        table_enable=str(table_enable).lower(),
        formula_enable=str(formula_enable).lower(),
        force_enable_ocr=str(force_enable_ocr).lower(),
    )

    logger.debug(f"API endpoint: {api_url}")
    logger.debug(
        f"Extraction parameters: parse_method={parse_method}, backend={backend}, "
        f"pages={start_page_id}-{end_page_id}, ocr={force_enable_ocr}, "
        f"tables={table_enable}, formulas={formula_enable}"
    )

    attempts = len(_RETRY_WAITS_SECONDS) + 1
    for attempt in range(1, attempts + 1):
        try:
            return _post_pdf(path, api_url, data)
        except Exception as e:
            if attempt < attempts and is_retryable(e):
                wait = _RETRY_WAITS_SECONDS[attempt - 1]
                logger.warning(
                    f"Transient MinerU failure (attempt {attempt}/{attempts}), "
                    f"retrying in {wait}s: {e}"
                )
                time.sleep(wait)
                continue
            raise


def _post_pdf(path: Path, api_url: str, data: dict) -> dict:
    """One POST of the PDF to file_parse; logs and re-raises any failure."""
    logger.debug("Sending PDF to API for processing...")
    response = None
    try:
        with open(path, "rb") as f:
            files = {"files": (path.name, f, "application/pdf")}

            response = get_session().post(
                api_url,
                data=data,
                files=files,
                headers={"accept": "application/json"},
                timeout=MINERU_TIMEOUT_SECONDS,
            )

        logger.debug(f"API response status: {response.status_code}")

        response.raise_for_status()
        result = response.json()

        logger.debug(
            f"API response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}"
        )
        logger.info(f"Successfully extracted content from {path.name}")

        return result

    except requests.exceptions.Timeout:
        logger.error(f"API request timed out after {MINERU_TIMEOUT_SECONDS} seconds")
        raise
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to API at {api_url}: {str(e)}")
        raise
    except requests.exceptions.HTTPError as e:
        error_msg = "API returned error status"
        if e.response is not None:
            error_msg += f" {e.response.status_code}: {e.response.text[:500]}"
        logger.error(error_msg)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during PDF extraction: {str(e)}")
        raise


def extract_pdf_text(pdf_path: str, **kwargs) -> str:
    """
    Extract text content from a PDF file as markdown string.

    Args:
        pdf_path: Path to the PDF file
        **kwargs: Additional arguments passed to extract_pdf_content()

    Returns:
        str: Extracted text content in markdown format
    """
    logger.debug(f"extract_pdf_text called for: {pdf_path}")
    try:
        result = extract_pdf_content(pdf_path, **kwargs)

        # Extract markdown content from API response
        if isinstance(result, dict):
            logger.debug(f"Response is dict with keys: {list(result.keys())}")

            # Try to extract md_content from nested structure
            md_content = None

            # Check top-level keys first
            md_content = (
                result.get("md_content") or result.get("content") or result.get("text")
            )

            # If not found, check inside 'results' key (common API structure)
            if not md_content and "results" in result:
                results = result["results"]
                if isinstance(results, dict):
                    # Get first sheet/page result
                    for sheet_name, sheet_data in results.items():
                        if isinstance(sheet_data, dict) and "md_content" in sheet_data:
                            md_content = sheet_data["md_content"]
                            logger.debug(f"Found md_content in results[{sheet_name}]")
                            break

            if md_content:
                content_length = (
                    len(md_content)
                    if isinstance(md_content, str)
                    else len(str(md_content))
                )
                logger.info(f"Extracted markdown content ({content_length} characters)")
                return md_content

            # If no direct content, return formatted JSON response
            logger.warning(
                "No markdown content found in response, returning formatted JSON"
            )
            return f"API Response:\n{result}"

        logger.warning(f"Unexpected response type: {type(result)}")
        return str(result)

    except Exception as e:
        logger.error(f"Error in extract_pdf_text: {str(e)}", exc_info=True)
        raise
