"""Post-OCR logical-document segmentation orchestrator (Option B).

Runs after PaddleOCR has written its native artifacts into the ``raw_<stem>``
folder. Chains the segmentation stages for a single source file:

    inventory -> text index -> classify -> segment -> organize -> writers

and returns a :class:`SegmentationResult` used by the pipeline to enrich the
per-file summary record (``documents_detected``).

The whole thing is defensive: it never raises to the caller. Any failure is
logged and captured in ``result.error`` so OCR success is not lost and the batch
continues.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .document_artifact_organizer import organize_documents
from .document_rules import DocumentRules
from .document_segmenter import segment_documents
from .llm_ready_writer import write_document_files
from .manifest_writer import write_source_manifest
from .page_classifier import classify_pages
from .page_inventory import build_inventory
from .page_text_indexer import build_text_index


@dataclass
class SegmentationResult:
    status: str = "ok"  # ok | failed
    total_pages: int = 0
    raw_output_folder: Optional[str] = None
    documents_detected: List[Dict] = field(default_factory=list)
    needs_review: bool = False
    error: Optional[str] = None

    def summary_documents(self) -> List[Dict]:
        """Compact per-document view for processing_summary.json."""
        return [
            {
                "document_id": d.get("document_id"),
                "document_type": d.get("document_type"),
                "pages": d.get("pages", []),
                "needs_review": bool(d.get("needs_review")),
            }
            for d in self.documents_detected
        ]


def segment_file(
    source_file: Path,
    output_dir: Path,
    raw_dir: Path,
    rules: DocumentRules,
    logger: logging.Logger,
    output_folder_display: Optional[str] = None,
    raw_folder_display: Optional[str] = None,
) -> SegmentationResult:
    """Segment one OCR'd file into logical documents.

    Args:
        source_file: original input file (provenance).
        output_dir: per-file output dir (``output/<folder>/<stem>/``).
        raw_dir: the ``raw_<stem>`` folder holding native OCR artifacts.
        rules: loaded document classification rules.
        logger: folder logger.
        output_folder_display: display path for the file output folder used in
            manifests (defaults to ``str(output_dir)``).
        raw_folder_display: display path for the raw folder (defaults to the
            raw dir relative to output_dir, else its str).
    """
    output_dir = Path(output_dir)
    raw_dir = Path(raw_dir)
    source_file = Path(source_file)

    if output_folder_display is None:
        output_folder_display = str(output_dir)
    if raw_folder_display is None:
        try:
            raw_folder_display = str(Path(output_folder_display) / raw_dir.relative_to(output_dir))
        except ValueError:
            raw_folder_display = str(raw_dir)

    result = SegmentationResult(raw_output_folder=raw_folder_display)

    try:
        logger.info("Segmentation START: %s", source_file.name)

        # 1) Inventory raw artifacts by page.
        inventory = build_inventory(raw_dir, source_file, output_dir, logger)
        result.total_pages = inventory.get("total_pages", 0)

        # 2) Per-page text index.
        text_index = build_text_index(inventory, output_dir, logger)

        # 3) Classify each page.
        classification = classify_pages(text_index, rules, output_dir, logger)

        # 4) Group pages into logical documents (mutates classification pages
        #    for continuation annotations, then rewrites classification json).
        groups = segment_documents(classification, rules, output_dir, logger)
        # Persist any continuation annotations back into page_classification.json.
        _rewrite_classification(classification, output_dir)

        # 5) Copy artifacts into documents/<NNN_tipo>/pages/page_NNN/.
        documents_dir = output_dir / "documents"
        organized = organize_documents(
            groups, inventory, output_dir, documents_dir, logger
        )

        # 6) Per-document metadata + llm_ready.
        doc_summaries = write_document_files(
            groups=groups,
            organized=organized,
            text_index=text_index,
            classification=classification,
            rules_types=rules.types,
            source_file=source_file,
            output_dir=output_dir,
            output_folder_display=output_folder_display,
            logger=logger,
        )

        # 7) Source manifest (primary ADK entry point).
        manifest = write_source_manifest(
            source_file=source_file,
            output_dir=output_dir,
            output_folder_display=output_folder_display,
            raw_folder_display=raw_folder_display,
            total_pages=result.total_pages,
            document_summaries=doc_summaries,
            logger=logger,
        )

        result.documents_detected = doc_summaries
        result.needs_review = bool(manifest.get("needs_review"))
        result.status = "ok"
        logger.info(
            "Segmentation DONE: %s -> %d document(s), needs_review=%s",
            source_file.name,
            len(doc_summaries),
            result.needs_review,
        )
    except Exception as exc:  # noqa: BLE001 - never break OCR/batch
        import traceback

        tb = traceback.format_exc()
        logger.error("Segmentation FAILED for %s: %s", source_file, exc)
        logger.error("Traceback:\n%s", tb)
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def _rewrite_classification(classification: Dict, output_dir: Path) -> None:
    """Re-persist page_classification.json after continuation annotations."""
    import json

    path = Path(output_dir) / "page_classification.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(classification, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
