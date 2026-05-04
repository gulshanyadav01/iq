"""Convert .xlsb (Excel binary) files to .xlsx via LibreOffice.

`pyxlsb` cannot read merged-cell ranges, which the rest of the extraction
pipeline relies on heavily. LibreOffice handles the format end-to-end and
preserves merges, so we shell out to `soffice --headless` and hand the
converted .xlsx to the existing pipeline.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class XlsbConversionError(Exception):
    pass


def convert_xlsb_to_xlsx(xlsb_path: str | os.PathLike, timeout: int = 120) -> str:
    """Convert an .xlsb file to .xlsx using LibreOffice headless.

    Returns the path to the converted .xlsx file. The caller owns the file
    and is responsible for deleting it.
    """
    src = Path(xlsb_path)
    if not src.is_file():
        raise XlsbConversionError(f"xlsb file not found: {src}")

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise XlsbConversionError(
            "LibreOffice (soffice) not found on PATH. "
            "Install libreoffice-calc to enable .xlsb support."
        )

    out_dir = Path(tempfile.mkdtemp(prefix="xlsb_convert_"))
    try:
        # LibreOffice writes to outdir using the source basename + .xlsx
        proc = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "xlsx",
                "--outdir",
                str(out_dir),
                str(src),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Isolate user profile so concurrent conversions don't collide
            env={
                **os.environ,
                "HOME": str(out_dir),
            },
        )
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise XlsbConversionError(
            f"LibreOffice conversion timed out after {timeout}s for {src.name}"
        ) from e

    if proc.returncode != 0:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise XlsbConversionError(
            f"LibreOffice conversion failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )

    converted = out_dir / f"{src.stem}.xlsx"
    if not converted.is_file():
        produced = list(out_dir.glob("*.xlsx"))
        if not produced:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise XlsbConversionError(
                f"LibreOffice produced no .xlsx output for {src.name}"
            )
        converted = produced[0]

    # Move out of the temp dir so we can clean it up, returning a stable path
    final_path = Path(tempfile.gettempdir()) / f"{converted.stem}_{os.getpid()}_{src.stat().st_size}.xlsx"
    shutil.move(str(converted), str(final_path))
    shutil.rmtree(out_dir, ignore_errors=True)

    logger.info(
        "Converted .xlsb to .xlsx via LibreOffice: %s -> %s (%d bytes)",
        src.name,
        final_path.name,
        final_path.stat().st_size,
    )
    return str(final_path)
