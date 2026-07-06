"""Fuse PaddleOCR and Tesseract results, box by box.

For every box shared between the two OCR passes (they always share the same
boxes, since Tesseract OCRs exactly the regions PaddleOCR detected), pick the
text from whichever source has the higher normalized confidence, subject to:

  1. Never select an empty string when the other source has text.
  2. If one source is empty, select the other.
  3. If the confidence difference is below ``tie_margin``, select
     ``tie_breaker`` (default ``paddleocr``).
  4. If the selected texts differ a lot (low similarity) while confidences are
     close, flag ``conflict_needs_review`` (a string is still chosen).
  5. Alternatives from both sources are always preserved in ``fusion_items``.

This module has no I/O beyond writing the fused JSON; box geometry
(``rec_box``/``rec_poly``) always comes from PaddleOCR, since Tesseract OCRs
those exact regions.
"""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import FusionConfig
from .utils.text_normalizer import normalize

INPUT_SOURCE = "paddleocr_tesseract_fusion"

SOURCE_PADDLEOCR = "paddleocr"
SOURCE_TESSERACT = "tesseract"

STATUS_SELECTED = "selected"
STATUS_CONFLICT = "conflict_needs_review"
STATUS_BOTH_EMPTY = "both_empty"


def _similarity(text_a: str, text_b: str) -> float:
    a, b = normalize(text_a), normalize(text_b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _choose(
    paddle_text: str,
    paddle_conf: float,
    tess_text: str,
    tess_conf: float,
    cfg: FusionConfig,
) -> Tuple[str, float, str, bool]:
    """Return (selected_text, selected_confidence, selected_source, is_conflict)."""
    paddle_text = paddle_text or ""
    tess_text = tess_text or ""
    paddle_conf = paddle_conf or 0.0
    tess_conf = tess_conf or 0.0

    paddle_empty = not paddle_text.strip()
    tess_empty = not tess_text.strip()

    if paddle_empty and tess_empty:
        # Nothing usable from either source; fall back to tie_breaker so the
        # output is deterministic, but there is no real text to select.
        if cfg.tie_breaker == SOURCE_TESSERACT:
            return tess_text, tess_conf, SOURCE_TESSERACT, False
        return paddle_text, paddle_conf, SOURCE_PADDLEOCR, False

    if paddle_empty:
        return tess_text, tess_conf, SOURCE_TESSERACT, False
    if tess_empty:
        return paddle_text, paddle_conf, SOURCE_PADDLEOCR, False

    diff = abs(paddle_conf - tess_conf)
    close = diff < cfg.tie_margin

    if close:
        source = cfg.tie_breaker if cfg.tie_breaker in (SOURCE_PADDLEOCR, SOURCE_TESSERACT) else SOURCE_PADDLEOCR
    else:
        source = SOURCE_PADDLEOCR if paddle_conf >= tess_conf else SOURCE_TESSERACT

    selected_text = paddle_text if source == SOURCE_PADDLEOCR else tess_text
    selected_conf = paddle_conf if source == SOURCE_PADDLEOCR else tess_conf

    is_conflict = False
    if cfg.mark_conflicts and close:
        similarity = _similarity(paddle_text, tess_text)
        if similarity <= cfg.conflict_text_similarity_max:
            is_conflict = True

    return selected_text, selected_conf, source, is_conflict


def fuse_page(
    paddle_extracted: Dict,
    tesseract_result: Dict,
    output_dir: Path,
    fusion_root: Path,
    cfg: FusionConfig,
    logger: logging.Logger,
) -> Optional[Dict]:
    """Fuse one page's PaddleOCR and Tesseract results.

    Args:
        paddle_extracted: output of ``tesseract_box_extractor.extract_boxes``.
        tesseract_result: output of ``tesseract_result_writer.process_page``.
        output_dir: per-file output dir (paths are written relative to it).
        fusion_root: absolute ``fusion/`` directory.
        cfg: fusion configuration.
        logger: folder logger.

    Returns:
        The fused dict (also written to
        ``fusion/page_XXX_overall_ocr_res_fused.json``), enriched with a
        ``_counts`` key used by :mod:`fusion_summary_writer`. ``None`` if
        inputs are missing/incompatible (isolated by the caller).
    """
    if not paddle_extracted or not tesseract_result:
        return None

    output_dir = Path(output_dir)
    page_index = paddle_extracted.get("page_index")

    tess_box_results = {
        b.get("box_index"): b for b in tesseract_result.get("box_results", [])
    }

    rec_texts: List[str] = []
    rec_scores: List[float] = []
    rec_boxes: List[Optional[list]] = []
    rec_polys: List[Optional[list]] = []
    selected_sources: List[str] = []
    fusion_items: List[Dict] = []

    counts = {
        "total_boxes": 0,
        "selected_from_paddleocr": 0,
        "selected_from_tesseract": 0,
        "conflicts": 0,
        "empty_tesseract_results": 0,
        "empty_paddleocr_results": 0,
    }

    for box in paddle_extracted.get("boxes", []):
        box_index = box.get("box_index")
        paddle_text = box.get("paddle_text") or ""
        paddle_conf = box.get("paddle_score") or 0.0
        rec_box = box.get("rec_box")
        rec_poly = box.get("rec_poly")

        tess_entry = tess_box_results.get(box_index) or {}
        tess_text = tess_entry.get("text") or ""
        tess_conf = tess_entry.get("confidence") or 0.0

        counts["total_boxes"] += 1
        if not paddle_text.strip():
            counts["empty_paddleocr_results"] += 1
        if not tess_text.strip():
            counts["empty_tesseract_results"] += 1

        selected_text, selected_conf, source, is_conflict = _choose(
            paddle_text, paddle_conf, tess_text, tess_conf, cfg
        )

        status = STATUS_SELECTED
        if not paddle_text.strip() and not tess_text.strip():
            status = STATUS_BOTH_EMPTY
        elif is_conflict:
            status = STATUS_CONFLICT
            counts["conflicts"] += 1

        if source == SOURCE_PADDLEOCR:
            counts["selected_from_paddleocr"] += 1
        else:
            counts["selected_from_tesseract"] += 1

        rec_texts.append(selected_text)
        rec_scores.append(round(float(selected_conf), 4))
        rec_boxes.append(rec_box)
        rec_polys.append(rec_poly)
        selected_sources.append(source)

        item = {
            "box_index": box_index,
            "bbox": rec_box,
            "selected_source": source,
            "selected_text": selected_text,
            "selected_confidence": round(float(selected_conf), 4),
            "status": status,
        }
        if cfg.keep_alternatives:
            item["paddleocr"] = {"text": paddle_text, "confidence": round(float(paddle_conf), 4)}
            item["tesseract"] = {"text": tess_text, "confidence": round(float(tess_conf), 4)}
        fusion_items.append(item)

    fused = {
        "input_source": INPUT_SOURCE,
        "page_index": page_index,
        "fusion_strategy": cfg.strategy,
        "sources": {
            "paddleocr": _rel_or_str(paddle_extracted.get("res_json"), output_dir),
            "tesseract": tesseract_result.get("_written_path"),
        },
        "rec_texts": rec_texts,
        "rec_scores": rec_scores,
        "rec_boxes": rec_boxes,
        "rec_polys": rec_polys,
        "selected_sources": selected_sources,
        "fusion_items": fusion_items,
    }

    fusion_root.mkdir(parents=True, exist_ok=True)
    from .utils.path_utils import page_folder_name

    out_path = fusion_root / f"{page_folder_name(page_index)}_overall_ocr_res_fused.json"
    _write_json(out_path, fused)
    logger.info(
        "ocr_fusion: page %s -> %d box(es) fused (paddle=%d, tesseract=%d, conflicts=%d) -> %s",
        page_index,
        counts["total_boxes"],
        counts["selected_from_paddleocr"],
        counts["selected_from_tesseract"],
        counts["conflicts"],
        out_path,
    )

    fused["_counts"] = counts
    fused["_written_path"] = str(out_path.relative_to(output_dir))
    return fused


def _rel_or_str(path, base: Path) -> Optional[str]:
    if not path:
        return None
    try:
        return str(Path(path).relative_to(base))
    except ValueError:
        return str(path)


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # _counts/_written_path are internal bookkeeping; keep them out of the
    # persisted JSON so the on-disk schema matches the plan exactly.
    persisted = {k: v for k, v in data.items() if not k.startswith("_")}
    with path.open("w", encoding="utf-8") as fh:
        json.dump(persisted, fh, ensure_ascii=False, indent=2)
