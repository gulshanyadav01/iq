"""
Chunked Parallel MinerU PDF → Markdown Service

Splits a large PDF into page-ranged chunks, fires the MinerU API calls in
parallel using start_page_id / end_page_id, and writes each chunk's markdown
to disk as soon as the response arrives. Returns the concatenated markdown
in page order.

Use this for large documents (hundreds of pages) where a single-shot call to
MineruGeminiParser._convert_pdf_to_markdown would exceed the 300s timeout.
"""

import os
import time
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

import pdfplumber


class MineruChunkedParser:
    MINERVA_API_URL = os.getenv(
        "MINERVA_API_URL",
        "https://rhobots.pbpartners.com/api/mineru/file_parse",
    )
    CHUNK_PAGES = int(os.getenv("MINERU_CHUNK_PAGES", "50"))
    MAX_WORKERS = int(os.getenv("MINERU_MAX_WORKERS", "4"))
    REQUEST_TIMEOUT = int(os.getenv("MINERU_CHUNK_TIMEOUT", "600"))

    BASE_FORM = {
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_md": "true",
        "return_images": "false",
        "parse_method": "auto",
        "lang_list": "en",
        "output_dir": "./output",
        "server_url": "string",
        "return_content_list": "false",
        "backend": "hybrid-auto-engine",
        "table_enable": "true",
        "response_format_zip": "false",
        "formula_enable": "true",
        "force_enable_ocr": "true",
    }

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir or "minerva_chunked_output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _page_count(self, pdf_path: str) -> int:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)

    def _chunk_ranges(self, total: int) -> List[Tuple[int, int]]:
        return [
            (s, min(s + self.CHUNK_PAGES - 1, total - 1))
            for s in range(0, total, self.CHUNK_PAGES)
        ]

    def _extract_md(self, payload) -> str:
        if isinstance(payload, dict):
            results = payload.get("results", {})
            if isinstance(results, dict):
                for _, v in results.items():
                    if isinstance(v, dict) and "md_content" in v:
                        return v["md_content"]
            for k in ("md_content", "md", "markdown", "content"):
                if k in payload:
                    return payload[k]
        elif isinstance(payload, str):
            return payload
        return ""

    def _process_chunk(
        self, pdf_path: str, idx: int, start: int, end: int
    ) -> Tuple[int, str]:
        with open(pdf_path, "rb") as f:
            files = {"files": (Path(pdf_path).name, f, "application/pdf")}
            data = {
                **self.BASE_FORM,
                "start_page_id": str(start),
                "end_page_id": str(end),
            }
            resp = requests.post(
                self.MINERVA_API_URL,
                files=files,
                data=data,
                headers={"accept": "application/json"},
                timeout=self.REQUEST_TIMEOUT,
            )
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                body = e.response.text if e.response is not None else ""
                raise requests.exceptions.HTTPError(
                    f"MinerU API error {status} for {self.MINERVA_API_URL}: {body}",
                    response=e.response,
                ) from e

        try:
            md = self._extract_md(resp.json())
        except ValueError:
            md = resp.text

        chunk_file = self.output_dir / f"chunk_{idx:04d}_p{start}-{end}.md"
        chunk_file.write_text(md, encoding="utf-8")
        return idx, md

    def convert_pdf_to_markdown(self, pdf_path: str) -> str:
        total = self._page_count(pdf_path)
        ranges = self._chunk_ranges(total)
        print(
            f"📄 Chunked MinerU: {Path(pdf_path).name} — "
            f"{total} pages → {len(ranges)} chunks "
            f"({self.CHUNK_PAGES}/chunk, {self.MAX_WORKERS} workers)"
        )

        results: Dict[int, str] = {}
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._process_chunk, pdf_path, i, s, e): (i, s, e)
                for i, (s, e) in enumerate(ranges)
            }
            for fut in as_completed(futures):
                i, s, e = futures[fut]
                try:
                    idx, md = fut.result()
                    results[idx] = md
                    print(
                        f"   ✓ chunk {idx + 1}/{len(ranges)} "
                        f"pages {s}-{e} ({len(md)} chars)"
                    )
                except Exception as exc:
                    print(f"   ✗ chunk {i} pages {s}-{e} failed: {exc}")
                    raise

        ordered = "\n\n".join(results[i] for i in sorted(results))
        (self.output_dir / "full.md").write_text(ordered, encoding="utf-8")
        print(
            f"   ✓ {total} pages → {len(ordered)} chars "
            f"in {time.time() - t0:.1f}s"
        )
        return ordered
