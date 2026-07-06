"""Write the per-file ``source_manifest.json``.

This is the primary entry point for the future ADK agent (and the next LLM
stage): it aggregates, for one source file, every logical document detected,
their page ranges, statuses, needs_review flags and the location of each
``llm_ready.json``. It also records the OCR sources used (PaddleOCR alone, or
PaddleOCR fused with Tesseract) and, when the fusion stage ran, a summary of
how many boxes were selected from each source.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

SPLIT_STRATEGY = "post_ocr_page_classification"
PAGE_UNIT = "page"


def write_source_manifest(
    source_file: Path,
    output_dir: Path,
    output_folder_display: str,
    raw_folder_display: str,
    total_pages: int,
    document_summaries: List[Dict],
    logger: logging.Logger,
    ocr_sources: Optional[Dict] = None,
    artifacts: Optional[Dict] = None,
    fusion_summary: Optional[Dict] = None,
) -> Dict:
    """Write ``source_manifest.json`` and return the manifest dict.

    Args:
        ocr_sources: e.g. ``{"primary": "paddleocr", "secondary": "tesseract",
            "fusion_enabled": bool, "fusion_strategy": str|None}``. Defaults to
            a PaddleOCR-only, fusion-disabled description when omitted.
        artifacts: folder names for ``raw_paddleocr_folder``,
            ``page_images_folder``, ``tesseract_folder``, ``fusion_folder``.
        fusion_summary: aggregate counters from
            :func:`fusion_summary_writer.aggregate_fusion_summary`, or ``None``
            when the fusion stage did not run.
    """
    source_file = Path(source_file)
    output_dir = Path(output_dir)

    documents_detected: List[Dict] = []
    any_needs_review = False
    for doc in document_summaries:
        any_needs_review = any_needs_review or bool(doc.get("needs_review"))
        documents_detected.append(
            {
                "document_id": doc.get("document_id"),
                "document_type": doc.get("document_type"),
                "document_label": doc.get("document_label"),
                "pages": doc.get("pages", []),
                "page_range": doc.get("page_range", ""),
                "confidence": doc.get("confidence", 0.0),
                "status": doc.get("status"),
                "needs_review": bool(doc.get("needs_review")),
                "output_folder": doc.get("output_folder"),
                "llm_ready": doc.get("llm_ready"),
            }
        )

    resolved_ocr_sources = {
        "primary": "paddleocr",
        "secondary": "tesseract",
        "fusion_enabled": False,
        "fusion_strategy": None,
    }
    if ocr_sources:
        resolved_ocr_sources.update(ocr_sources)

    resolved_artifacts = {
        "raw_paddleocr_folder": raw_folder_display,
        "page_images_folder": None,
        "tesseract_folder": None,
        "fusion_folder": None,
    }
    if artifacts:
        resolved_artifacts.update(artifacts)

    manifest = {
        "source_file": str(source_file),
        "source_file_name": source_file.name,
        "file_output_folder": output_folder_display,
        "raw_output_folder": raw_folder_display,
        "split_strategy": SPLIT_STRATEGY,
        "page_unit": PAGE_UNIT,
        "total_pages": total_pages,
        "documents_detected": documents_detected,
        "needs_review": any_needs_review,
        "ocr_sources": resolved_ocr_sources,
        "artifacts": resolved_artifacts,
        "fusion_summary": fusion_summary,
    }

    out_path = output_dir / "source_manifest.json"
    _write_json(out_path, manifest)
    logger.info(
        "Wrote source_manifest.json (%d document(s), needs_review=%s, fusion_enabled=%s) -> %s",
        len(documents_detected),
        any_needs_review,
        resolved_ocr_sources.get("fusion_enabled"),
        out_path,
    )
    return manifest


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
