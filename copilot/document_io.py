"""Bounded local document readers. No document content is sent to an API."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

MAX_PAGES = 150
MAX_OCR_PAGES = 40
MAX_RENDER_PIXELS = 16_000_000
OCR_TIMEOUT_SECONDS = 45


def page_needs_ocr(page) -> bool:
    """Detect image-only pages and full-page scans with hidden OCR layers.

    Visible native text wins on ordinary mixed-layout pages. Embedded image
    tables on such pages remain a separate, unsupported extraction problem.
    """
    traces = page.get_texttrace()
    visible = sum(len(t["chars"]) for t in traces if t["type"] != 3 and t.get("opacity", 1) > 0)
    hidden = sum(len(t["chars"]) for t in traces if t["type"] == 3 or t.get("opacity", 1) == 0)
    images = page.get_image_info()
    if not images:
        return False
    if not visible:
        return True
    import pymupdf
    area = abs(page.rect)
    coverage = max((abs(pymupdf.Rect(i["bbox"]) & page.rect) / area for i in images), default=0) if area else 0
    return coverage >= 0.85 and hidden > visible


def local_ocr(page) -> str:
    """Render only the selected page; bound raster size and subprocess time."""
    import math
    import pymupdf
    area = max(1, abs(page.rect))
    scale = min(300 / 72, math.sqrt(MAX_RENDER_PIXELS / area))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", os.getenv("COPILOT_OCR_LANG", "eng"), "--psm", "3"],
            input=pix.tobytes("png"), capture_output=True, check=True, timeout=OCR_TIMEOUT_SECONDS,
            env={**os.environ, "OMP_THREAD_LIMIT": "2"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Local OCR failed or is unavailable. Install Tesseract and the configured language pack; inspect the source page.") from exc
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("OCR returned no text on a scanned page; review the source manually.")
    return text


def legacy_word_text(path: str | Path) -> str:
    """Read legacy binary Word locally; no macros are executed."""
    path = Path(path).resolve()
    with path.open("rb") as f:
        if f.read(8) != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise ValueError("Unsupported or invalid legacy Word document.")
    try:
        result = subprocess.run(["antiword", "-m", "UTF-8.txt", str(path)],
                                capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Legacy Word extraction failed. Install antiword or convert the file locally to DOCX.") from exc
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("No readable legacy Word text; convert to DOCX and inspect manually.")
    return text
