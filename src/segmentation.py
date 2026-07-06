"""Post-OCR logical-document segmentation orchestrator (Option B).

Runs after PaddleOCR has written its native artifacts into the ``raw_<stem>``
folder. Chains the segmentation stages for a single source file:

    inventory -> [tesseract fusion] -> text index -> classify -> segment
    -> organize -> writers

and returns a :class:`SegmentationResult` used by the pipeline to enrich the
per-file summary record (``documents_detected``, fusion stats).

The whole thing is defensive: it never raises to the caller. Any failure is
logged and captured in ``result.error`` so OCR success is not lost and the batch
continues.

Tesseract fusion stage (optional, per-page):
  render clean page image -> validate size vs PaddleOCR JSON -> extract boxes
  -> crop -> Tesseract OCR -> overall_ocr_res_tesseract.json -> fuse with
  PaddleOCR -> overall_ocr_res_fused.json. Failures are isolated per page; if
  disabled/unavailable, this stage is skipped entirely and downstream stages
  behave exactly as before (PaddleOCR-only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import tesseract_box_extractor, tesseract_result_writer, tesseract_runner
from .config import FusionConfig, PageImagesConfig, TesseractConfig
from .document_artifact_organizer import organize_documents
from .document_rules import DocumentRules
from .document_segmenter import segment_documents
from .fusion_summary_writer import aggregate_fusion_summary
from .llm_ready_writer import write_document_files
from .manifest_writer import write_source_manifest
from .ocr_fusion import fuse_page
from .page_classifier import classify_pages
from .page_image_renderer import render_pages
from .page_image_validator import ValidationResult, validate_base_image
from .page_inventory import build_inventory
from .page_text_indexer import build_text_index

TESSERACT_FOLDER_NAME = "tesseract"
FUSION_FOLDER_NAME = "fusion"


@dataclass
class SegmentationResult:
    status: str = "ok"  # ok | failed
    total_pages: int = 0
    raw_output_folder: Optional[str] = None
    documents_detected: List[Dict] = field(default_factory=list)
    needs_review: bool = False
    error: Optional[str] = None

    # Tesseract fusion stage.
    fusion_attempted: bool = False
    tesseract_run: bool = False
    page_images_folder: Optional[str] = None
    tesseract_folder: Optional[str] = None
    fusion_folder: Optional[str] = None
    fusion_summary: Dict = field(default_factory=dict)

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


@dataclass
class _FusionStageResult:
    """Internal outcome of the Tesseract fusion stage for one file."""

    attempted: bool = False
    tesseract_run: bool = False
    page_images_folder: Optional[str] = None
    tesseract_folder: Optional[str] = None
    fusion_folder: Optional[str] = None
    fusion_summary: Optional[Dict] = None


def segment_file(
    source_file: Path,
    output_dir: Path,
    raw_dir: Path,
    rules: DocumentRules,
    logger: logging.Logger,
    output_folder_display: Optional[str] = None,
    raw_folder_display: Optional[str] = None,
    ocr_input: Optional[Path] = None,
    page_images_cfg: Optional[PageImagesConfig] = None,
    tesseract_cfg: Optional[TesseractConfig] = None,
    fusion_cfg: Optional[FusionConfig] = None,
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
        ocr_input: the file actually fed to PaddleOCR (post office-document
            conversion, if any). Used as the source for clean per-page image
            rendering in the Tesseract fusion stage. When omitted, the
            renderer falls back to each page's ``_res.json.input_path``.
        page_images_cfg/tesseract_cfg/fusion_cfg: configuration for the
            Tesseract fusion stage. When any of the three is ``None``, the
            fusion stage is skipped entirely and behavior is identical to
            PaddleOCR-only segmentation.
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

        # 1b) Optional Tesseract fusion stage (annotates inventory in place
        #     with fused_json/tesseract_json per page). Never raises.
        fusion_stage = _run_fusion_stage(
            ocr_input=ocr_input,
            inventory=inventory,
            output_dir=output_dir,
            page_images_cfg=page_images_cfg,
            tesseract_cfg=tesseract_cfg,
            fusion_cfg=fusion_cfg,
            logger=logger,
        )
        result.fusion_attempted = fusion_stage.attempted
        result.tesseract_run = fusion_stage.tesseract_run
        result.page_images_folder = fusion_stage.page_images_folder
        result.tesseract_folder = fusion_stage.tesseract_folder
        result.fusion_folder = fusion_stage.fusion_folder
        result.fusion_summary = fusion_stage.fusion_summary or {}
        # The fusion stage annotates inventory["pages"][i] with fused_json /
        # tesseract_json in place; persist those annotations back to disk.
        if fusion_stage.attempted:
            _rewrite_inventory(inventory, output_dir)

        # 2) Per-page text index (prefers the fused result when present).
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
            ocr_sources={
                "primary": "paddleocr",
                "secondary": "tesseract",
                "fusion_enabled": result.fusion_attempted,
                "fusion_strategy": fusion_cfg.strategy if fusion_cfg else None,
            },
            artifacts={
                "raw_paddleocr_folder": raw_folder_display,
                "page_images_folder": result.page_images_folder,
                "tesseract_folder": result.tesseract_folder,
                "fusion_folder": result.fusion_folder,
            },
            fusion_summary=result.fusion_summary or None,
        )

        result.documents_detected = doc_summaries
        result.needs_review = bool(manifest.get("needs_review"))
        result.status = "ok"
        logger.info(
            "Segmentation DONE: %s -> %d document(s), needs_review=%s, tesseract_run=%s",
            source_file.name,
            len(doc_summaries),
            result.needs_review,
            result.tesseract_run,
        )
    except Exception as exc:  # noqa: BLE001 - never break OCR/batch
        import traceback

        tb = traceback.format_exc()
        logger.error("Segmentation FAILED for %s: %s", source_file, exc)
        logger.error("Traceback:\n%s", tb)
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def _run_fusion_stage(
    ocr_input: Optional[Path],
    inventory: Dict,
    output_dir: Path,
    page_images_cfg: Optional[PageImagesConfig],
    tesseract_cfg: Optional[TesseractConfig],
    fusion_cfg: Optional[FusionConfig],
    logger: logging.Logger,
) -> _FusionStageResult:
    """Run the render -> crop -> Tesseract -> fusion flow for every page.

    Annotates each ``inventory["pages"][i]`` in place with ``fused_json`` and
    ``tesseract_json`` (relative paths) on success. Never raises: any failure
    (global or per-page) is logged and isolated so the rest of segmentation
    proceeds using PaddleOCR results only.
    """
    stage = _FusionStageResult()

    if page_images_cfg is None or tesseract_cfg is None or fusion_cfg is None:
        logger.debug("Fusion stage: not configured for this run; skipping.")
        return stage

    if not (fusion_cfg.enabled and tesseract_cfg.enabled and page_images_cfg.enabled):
        logger.info(
            "Fusion stage skipped (fusion.enabled=%s, tesseract.enabled=%s, "
            "page_images.enabled=%s)",
            fusion_cfg.enabled,
            tesseract_cfg.enabled,
            page_images_cfg.enabled,
        )
        return stage

    if not tesseract_runner.is_available(tesseract_cfg):
        logger.warning(
            "Fusion stage skipped: tesseract binary not available (%s). "
            "Falling back to PaddleOCR-only results.",
            tesseract_cfg.executable_path,
        )
        return stage

    stage.attempted = True
    stage.page_images_folder = page_images_cfg.output_folder_name
    stage.tesseract_folder = TESSERACT_FOLDER_NAME
    stage.fusion_folder = FUSION_FOLDER_NAME

    try:
        page_images = render_pages(ocr_input, inventory, output_dir, page_images_cfg, logger)
    except Exception as exc:  # noqa: BLE001 - never break the file
        logger.error("Fusion stage: page rendering failed entirely: %s", exc)
        stage.fusion_summary = aggregate_fusion_summary([], 0, len(inventory.get("pages", [])))
        return stage

    tesseract_root = output_dir / stage.tesseract_folder
    fusion_root = output_dir / stage.fusion_folder

    fused_pages: List[Dict] = []
    pages_processed = 0
    pages_failed = 0

    for page_entry in inventory.get("pages", []):
        page_index = page_entry.get("page_index")
        try:
            page_image = page_images.get(page_index)
            if page_image is None or page_image.status != "ok":
                raise RuntimeError(
                    f"page image not available (status="
                    f"{getattr(page_image, 'status', 'missing')})"
                )

            res_rel = page_entry.get("res_json")
            if not res_rel:
                raise RuntimeError("no res_json recorded for this page")
            res_json_path = Path(res_rel)
            if not res_json_path.is_absolute():
                res_json_path = output_dir / res_json_path

            if page_image.json_width and page_image.json_height:
                validation = validate_base_image(
                    json_width=page_image.json_width,
                    json_height=page_image.json_height,
                    image_width=page_image.width,
                    image_height=page_image.height,
                    suspicious_panel_ratio=page_images_cfg.suspicious_panel_ratio,
                    allow_box_scaling=page_images_cfg.allow_box_scaling,
                )
            else:
                # Renderer had no JSON size to target (e.g. single-image
                # source); the image IS the page, so treat it as a direct match.
                validation = ValidationResult(mode="direct", scale_x=1.0, scale_y=1.0)

            if validation.mode == "reject":
                raise RuntimeError(validation.reason or "base image rejected")

            extracted = tesseract_box_extractor.extract_boxes(res_json_path, logger)
            if extracted is None:
                raise RuntimeError("failed to extract PaddleOCR boxes from _res.json")

            tess_result = tesseract_result_writer.process_page(
                res_json_path=res_json_path,
                page_image=page_image,
                validation=validation,
                output_dir=output_dir,
                tesseract_root=tesseract_root,
                cfg=tesseract_cfg,
                logger=logger,
            )
            if tess_result is None:
                raise RuntimeError("tesseract_result_writer produced no result")

            fused = fuse_page(
                paddle_extracted=extracted,
                tesseract_result=tess_result,
                output_dir=output_dir,
                fusion_root=fusion_root,
                cfg=fusion_cfg,
                logger=logger,
            )
            if fused is None:
                raise RuntimeError("ocr_fusion produced no result")

            page_entry["fused_json"] = fused.get("_written_path")
            page_entry["tesseract_json"] = tess_result.get("_written_path")
            fused_pages.append(fused)
            pages_processed += 1
        except Exception as exc:  # noqa: BLE001 - isolate per-page failures
            logger.error("Fusion stage failed for page %s: %s", page_index, exc)
            pages_failed += 1
            continue

    stage.tesseract_run = pages_processed > 0
    stage.fusion_summary = aggregate_fusion_summary(fused_pages, pages_processed, pages_failed)
    return stage


def _rewrite_inventory(inventory: Dict, output_dir: Path) -> None:
    """Re-persist page_inventory.json after fusion annotations (fused_json/tesseract_json)."""
    import json

    path = Path(output_dir) / "page_inventory.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(inventory, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _rewrite_classification(classification: Dict, output_dir: Path) -> None:
    """Re-persist page_classification.json after continuation annotations."""
    import json

    path = Path(output_dir) / "page_classification.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(classification, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
