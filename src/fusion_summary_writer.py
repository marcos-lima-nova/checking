"""Aggregate per-page fusion counters into a file-level ``fusion_summary``.

Pure utility (no I/O): callers pass in the list of per-page fusion dicts (as
returned by :func:`ocr_fusion.fuse_page`, which include a private ``_counts``
key) and get back the aggregate structure used by both
``source_manifest.json`` and ``processing_summary.json``.
"""

from __future__ import annotations

from typing import Dict, List

_COUNT_KEYS = (
    "total_boxes",
    "selected_from_paddleocr",
    "selected_from_tesseract",
    "conflicts",
    "empty_tesseract_results",
    "empty_paddleocr_results",
)


def aggregate_fusion_summary(
    fused_pages: List[Dict],
    pages_processed: int,
    pages_failed: int,
) -> Dict:
    """Sum per-page ``_counts`` into a single ``fusion_summary`` dict.

    Args:
        fused_pages: list of fused-page dicts (each carrying a ``_counts`` key
            with the per-page tallies produced by ``ocr_fusion.fuse_page``).
        pages_processed: number of pages that went through the fusion flow
            successfully (render + boxes + tesseract + fusion all succeeded).
        pages_failed: number of pages where the fusion flow failed/was skipped.

    Returns:
        A dict with the summed counters plus ``pages_processed``/``pages_failed``.
    """
    totals = {key: 0 for key in _COUNT_KEYS}
    for page in fused_pages:
        counts = page.get("_counts") or {}
        for key in _COUNT_KEYS:
            totals[key] += int(counts.get(key, 0) or 0)

    totals["pages_processed"] = pages_processed
    totals["pages_failed"] = pages_failed
    return totals
