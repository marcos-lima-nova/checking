"""Extract PaddleOCR boxes from a ``*_res.json`` for per-box Tesseract OCR.

Reads ``overall_ocr_res.{rec_texts,rec_scores,rec_boxes,rec_polys}`` and turns
them into a simple, index-preserving structure that the cropper/runner/fusion
modules consume. The PaddleOCR text/score/box for each box is preserved
verbatim (it is one of the two candidates compared during fusion).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional


def _as_plain_box(value):
    if value is None:
        return None
    if isinstance(value, list):
        return value
    try:
        return list(value)
    except TypeError:
        return None


def extract_boxes(res_json_path: Path, logger: Optional[logging.Logger] = None) -> Optional[Dict]:
    """Parse one ``*_res.json`` into a page/boxes structure.

    Returns ``None`` (and logs) if the file is missing or malformed. Boxes with
    missing score/box/poly entries are still included (with ``None`` values)
    so ``box_index`` stays aligned with PaddleOCR's own arrays.
    """
    res_json_path = Path(res_json_path)
    if not res_json_path.exists():
        if logger:
            logger.warning("tesseract_box_extractor: res_json missing: %s", res_json_path)
        return None

    try:
        with res_json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        if logger:
            logger.error("tesseract_box_extractor: failed to read %s: %s", res_json_path, exc)
        return None

    page_index = data.get("page_index")
    width = data.get("width")
    height = data.get("height")

    ocr = data.get("overall_ocr_res") or {}
    texts = ocr.get("rec_texts") or []
    scores = ocr.get("rec_scores") or []
    boxes = ocr.get("rec_boxes") or []
    polys = ocr.get("rec_polys") or []

    n = len(texts)
    boxes_out: List[Dict] = []
    for i in range(n):
        score = scores[i] if i < len(scores) else None
        rec_box = _as_plain_box(boxes[i]) if i < len(boxes) else None
        rec_poly = _as_plain_box(polys[i]) if i < len(polys) else None
        boxes_out.append(
            {
                "box_index": i,
                "paddle_text": texts[i],
                "paddle_score": float(score) if isinstance(score, (int, float)) else score,
                "rec_box": rec_box,
                "rec_poly": rec_poly,
            }
        )

    return {
        "page_index": page_index,
        "width": width,
        "height": height,
        "res_json": str(res_json_path),
        "boxes": boxes_out,
    }
