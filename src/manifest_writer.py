"""Write the per-file ``source_manifest.json``.

This is the primary entry point for the future ADK agent (and the next LLM
stage): it aggregates, for one source file, every logical document detected,
their page ranges, statuses, needs_review flags and the location of each
``llm_ready.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

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
) -> Dict:
    """Write ``source_manifest.json`` and return the manifest dict."""
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
    }

    out_path = output_dir / "source_manifest.json"
    _write_json(out_path, manifest)
    logger.info(
        "Wrote source_manifest.json (%d document(s), needs_review=%s) -> %s",
        len(documents_detected),
        any_needs_review,
        out_path,
    )
    return manifest


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
