"""Orchestrate per-page Tesseract OCR and write ``overall_ocr_res_tesseract.json``.

For one page, this ties together:
  * the boxes extracted from PaddleOCR's ``*_res.json``
    (``tesseract_box_extractor``);
  * cropping those boxes out of the clean ``page_images/page_XXX.png``
    (``box_cropper``);
  * running Tesseract on each crop (``tesseract_runner``).

The resulting JSON preserves the PaddleOCR ``rec_boxes``/``rec_polys`` (Tesseract
OCRs the *same* regions the PaddleOCR boxes describe), and adds Tesseract's own
text/confidence per box plus a ``box_results`` array with per-box status and the
crop path for traceability.

Per-box failures never abort the page; a failed box is recorded with
``status="failed"``/empty text/``0.0`` confidence.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from . import box_cropper, tesseract_box_extractor, tesseract_runner
from .config import TesseractConfig
from .page_image_renderer import PageImage
from .page_image_validator import ValidationResult
from .utils.path_utils import page_folder_name

INPUT_SOURCE = "tesseract_box_ocr"


def process_page(
    res_json_path: Path,
    page_image: PageImage,
    validation: ValidationResult,
    output_dir: Path,
    tesseract_root: Path,
    cfg: TesseractConfig,
    logger: logging.Logger,
) -> Optional[Dict]:
    """Run the full Tesseract-per-box flow for one page.

    Returns the in-memory ``overall_ocr_res_tesseract`` dict (also written to
    disk), or ``None`` if boxes could not be extracted at all (page-level
    failure, isolated by the caller).
    """
    output_dir = Path(output_dir)
    page_index = page_image.page_index

    extracted = tesseract_box_extractor.extract_boxes(res_json_path, logger)
    if extracted is None:
        return None

    page_dir_name = page_folder_name(page_index)
    page_dir = tesseract_root / page_dir_name
    image_path = output_dir / page_image.path if page_image.path else None

    box_results = []
    rec_texts, rec_scores, rec_boxes, rec_polys = [], [], [], []

    if image_path is None or not image_path.exists():
        logger.error(
            "tesseract_result_writer: page image missing for page %s; "
            "skipping Tesseract for this page",
            page_index,
        )
        crops = [
            box_cropper.CropResult(box_index=b["box_index"], status="failed", error="no page image")
            for b in extracted["boxes"]
        ]
    else:
        crops = box_cropper.crop_boxes(
            page_image_path=image_path,
            boxes=extracted["boxes"],
            mode=validation.mode,
            scale_x=validation.scale_x,
            scale_y=validation.scale_y,
            page_dir=page_dir,
            output_dir=output_dir,
            cfg=cfg,
            logger=logger,
        )

    crops_by_index = {c.box_index: c for c in crops}

    for box in extracted["boxes"]:
        box_index = box["box_index"]
        crop = crops_by_index.get(box_index)
        rec_box = box.get("rec_box")
        rec_poly = box.get("rec_poly")

        if crop is None or crop.status != "ok" or crop.image is None:
            text, confidence, status = "", 0.0, "failed"
        else:
            text, confidence, status = tesseract_runner.ocr_crop(crop.image, cfg, logger)

        rec_texts.append(text)
        rec_scores.append(confidence)
        rec_boxes.append(rec_box)
        rec_polys.append(rec_poly)

        box_result = {
            "box_index": box_index,
            "text": text,
            "confidence": confidence,
            "status": status,
            "crop_path": crop.crop_path if crop else None,
        }
        box_results.append(box_result)

        if cfg.save_box_json:
            _write_json(page_dir / f"box_{box_index:03d}.json", box_result)

    result = {
        "input_source": INPUT_SOURCE,
        "page_index": page_index,
        "based_on": {
            "paddle_res_json": _rel_or_str(res_json_path, output_dir),
            "image_source": page_image.path,
        },
        "rec_texts": rec_texts,
        "rec_scores": rec_scores,
        "rec_boxes": rec_boxes,
        "rec_polys": rec_polys,
        "box_results": box_results,
    }

    out_path = page_dir / "overall_ocr_res_tesseract.json"
    _write_json(out_path, result)
    logger.info(
        "tesseract_result_writer: page %s -> %d box(es) OCR'd -> %s",
        page_index,
        len(box_results),
        out_path,
    )
    result["_written_path"] = str(out_path.relative_to(output_dir))
    return result


def _rel_or_str(path: Path, base: Path) -> str:
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return str(path)


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
