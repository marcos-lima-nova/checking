"""Extract and normalize per-page text from raw OCR JSON.

For each page in the inventory, this reads the corresponding ``*_res.json`` and
pulls the recognized text from ``overall_ocr_res`` (``rec_texts`` / ``rec_scores``
/ ``rec_boxes``). It produces:

  * ``raw_text_lines``: original text with score and bbox preserved;
  * ``normalized_text``: a normalized blob used by the classifier;
  * ``low_confidence_lines``: lines below a score threshold (kept separately).

Output: ``page_text_index.json`` plus an in-memory structure for the classifier.

Per-page failures are isolated: a page whose JSON cannot be read is recorded
with empty text and an ``error`` field, and processing continues.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .utils.text_normalizer import normalize, normalize_lines

# Lines scoring below this are considered low-confidence and set aside.
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5


def _resolve_artifact(rel_or_abs: Optional[str], output_dir: Path) -> Optional[Path]:
    """Turn an inventory path (relative to output_dir, or absolute) into a Path."""
    if not rel_or_abs:
        return None
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return output_dir / p


def _extract_lines(res_data: Dict) -> List[Dict]:
    """Pull (text, score, bbox) triples from ``overall_ocr_res``."""
    ocr = res_data.get("overall_ocr_res") or {}
    texts = ocr.get("rec_texts") or []
    scores = ocr.get("rec_scores") or []
    boxes = ocr.get("rec_boxes") or []

    lines: List[Dict] = []
    for i, text in enumerate(texts):
        score = scores[i] if i < len(scores) else None
        bbox = boxes[i] if i < len(boxes) else None
        # Coerce numpy-ish/box types into JSON-friendly plain lists.
        if bbox is not None and not isinstance(bbox, list):
            try:
                bbox = list(bbox)
            except TypeError:
                bbox = None
        lines.append(
            {
                "text": text,
                "score": float(score) if isinstance(score, (int, float)) else score,
                "bbox": bbox,
            }
        )
    return lines


def build_text_index(
    inventory: Dict,
    output_dir: Path,
    logger: logging.Logger,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> Dict:
    """Build the per-page text index from the inventory.

    Returns the index dict (also written to ``output_dir/page_text_index.json``).
    """
    output_dir = Path(output_dir)
    pages_out: List[Dict] = []

    for page in inventory.get("pages", []):
        page_index = page.get("page_index")
        res_path = _resolve_artifact(page.get("res_json"), output_dir)

        entry: Dict = {
            "page_index": page_index,
            "raw_text_lines": [],
            "normalized_text": "",
            "low_confidence_lines": [],
        }

        if res_path is None or not res_path.exists():
            entry["error"] = "res_json missing"
            logger.warning("Text index: res_json missing for page %s", page_index)
            pages_out.append(entry)
            continue

        try:
            with res_path.open("r", encoding="utf-8") as fh:
                res_data = json.load(fh)
            lines = _extract_lines(res_data)

            raw_lines: List[Dict] = []
            low_conf: List[Dict] = []
            for ln in lines:
                score = ln.get("score")
                is_low = isinstance(score, (int, float)) and score < low_confidence_threshold
                if is_low:
                    low_conf.append({"text": ln["text"], "score": ln["score"]})
                raw_lines.append(ln)

            entry["raw_text_lines"] = raw_lines
            entry["normalized_text"] = normalize_lines(ln["text"] for ln in raw_lines)
            entry["low_confidence_lines"] = low_conf
        except Exception as exc:  # noqa: BLE001 - isolate per-page failures
            entry["error"] = f"{type(exc).__name__}: {exc}"
            logger.error("Text index failed for page %s: %s", page_index, exc)

        pages_out.append(entry)

    index = {
        "source_file": inventory.get("source_file"),
        "low_confidence_threshold": low_confidence_threshold,
        "pages": pages_out,
    }

    out_path = output_dir / "page_text_index.json"
    _write_json(out_path, index)
    logger.info("Wrote page_text_index.json (%d page(s)) -> %s", len(pages_out), out_path)
    return index


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
