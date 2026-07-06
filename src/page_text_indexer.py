"""Extract and normalize per-page text from OCR JSON, preferring fused results.

For each page in the inventory, this pulls per-line text/score/bbox from the
best available source, in priority order:

  1. ``fusion/page_XXX_overall_ocr_res_fused.json`` (PaddleOCR x Tesseract
     fusion), when the fusion stage ran for that page;
  2. PaddleOCR ``overall_ocr_res`` from the page's ``*_res.json`` (unchanged
     v1 behavior — the default when fusion is disabled/unavailable);
  3. Tesseract ``overall_ocr_res_tesseract.json``, if somehow the fused file is
     missing but a Tesseract result exists;
  4. ``parsing_res_list`` from the ``*_res.json``;
  5. ``table_res_list[].pred_html``;
  6. the page's markdown file.

It produces:

  * ``raw_text_lines``: text with score, bbox, ``source`` and ``fusion_status``;
  * ``normalized_text``: a normalized blob used by the classifier;
  * ``low_confidence_lines``: lines below a score threshold (kept separately).

Output: ``page_text_index.json`` plus an in-memory structure for the classifier.

Per-page failures are isolated: a page whose sources cannot be read is recorded
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

SOURCE_FUSED = "fused"
SOURCE_PADDLEOCR = "paddleocr"
SOURCE_TESSERACT = "tesseract"
SOURCE_PARSING = "parsing"
SOURCE_TABLE = "table"
SOURCE_MARKDOWN = "markdown"


def _resolve_artifact(rel_or_abs: Optional[str], output_dir: Path) -> Optional[Path]:
    """Turn an inventory path (relative to output_dir, or absolute) into a Path."""
    if not rel_or_abs:
        return None
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return output_dir / p


def _load_json(path: Optional[Path]) -> Optional[Dict]:
    if path is None or not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _coerce_bbox(bbox):
    if bbox is None or isinstance(bbox, list):
        return bbox
    try:
        return list(bbox)
    except TypeError:
        return None


def _lines_from_fused(fused: Dict) -> List[Dict]:
    """Pull lines from a fused JSON: text/score/bbox + selected source/status."""
    texts = fused.get("rec_texts") or []
    scores = fused.get("rec_scores") or []
    boxes = fused.get("rec_boxes") or []
    items = fused.get("fusion_items") or []
    items_by_index = {it.get("box_index"): it for it in items}

    lines: List[Dict] = []
    for i, text in enumerate(texts):
        score = scores[i] if i < len(scores) else None
        bbox = _coerce_bbox(boxes[i] if i < len(boxes) else None)
        item = items_by_index.get(i, {})
        lines.append(
            {
                "text": text,
                "score": float(score) if isinstance(score, (int, float)) else score,
                "bbox": bbox,
                "source": item.get("selected_source") or SOURCE_FUSED,
                "fusion_status": item.get("status"),
            }
        )
    return lines


def _lines_from_overall_ocr_res(ocr: Dict, source: str) -> List[Dict]:
    """Pull (text, score, bbox) triples from a PaddleOCR/Tesseract ``overall_ocr_res``."""
    texts = ocr.get("rec_texts") or []
    scores = ocr.get("rec_scores") or []
    boxes = ocr.get("rec_boxes") or []

    lines: List[Dict] = []
    for i, text in enumerate(texts):
        score = scores[i] if i < len(scores) else None
        bbox = _coerce_bbox(boxes[i] if i < len(boxes) else None)
        lines.append(
            {
                "text": text,
                "score": float(score) if isinstance(score, (int, float)) else score,
                "bbox": bbox,
                "source": source,
                "fusion_status": None,
            }
        )
    return lines


def _lines_from_parsing_res_list(res_data: Dict) -> List[Dict]:
    """Fallback: pull plain text blocks from ``parsing_res_list`` (no bbox/score)."""
    parsing = res_data.get("parsing_res_list") or []
    lines: List[Dict] = []
    for block in parsing:
        text = None
        if isinstance(block, dict):
            text = block.get("block_content") or block.get("text")
        elif isinstance(block, str):
            text = block
        if text:
            lines.append({"text": text, "score": None, "bbox": None, "source": SOURCE_PARSING, "fusion_status": None})
    return lines


def _lines_from_table_res_list(res_data: Dict) -> List[Dict]:
    """Fallback: pull raw HTML strings from ``table_res_list[].pred_html``."""
    tables = res_data.get("table_res_list") or []
    lines: List[Dict] = []
    for table in tables:
        html = table.get("pred_html") if isinstance(table, dict) else None
        if html:
            lines.append({"text": html, "score": None, "bbox": None, "source": SOURCE_TABLE, "fusion_status": None})
    return lines


def _lines_from_markdown(markdown_path: Optional[Path]) -> List[Dict]:
    if markdown_path is None or not markdown_path.exists():
        return []
    try:
        text = markdown_path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [
        {"text": ln, "score": None, "bbox": None, "source": SOURCE_MARKDOWN, "fusion_status": None}
        for ln in lines
    ]


def _extract_lines_with_fallback(
    page: Dict,
    output_dir: Path,
    logger: logging.Logger,
) -> List[Dict]:
    """Resolve one page's lines following the documented source priority."""
    page_index = page.get("page_index")

    # 1) Fused PaddleOCR x Tesseract result.
    fused_path = _resolve_artifact(page.get("fused_json"), output_dir)
    fused = _load_json(fused_path)
    if fused:
        lines = _lines_from_fused(fused)
        if lines:
            return lines
        logger.debug("Text index: fused result empty for page %s; falling back", page_index)

    # 2) PaddleOCR overall_ocr_res (default / unchanged v1 behavior).
    res_path = _resolve_artifact(page.get("res_json"), output_dir)
    res_data = _load_json(res_path)
    if res_data:
        ocr = res_data.get("overall_ocr_res") or {}
        lines = _lines_from_overall_ocr_res(ocr, SOURCE_PADDLEOCR)
        if lines:
            return lines

    # 3) Tesseract overall_ocr_res_tesseract.json (fused missing but tess ran).
    tess_path = _resolve_artifact(page.get("tesseract_json"), output_dir)
    tess_data = _load_json(tess_path)
    if tess_data:
        lines = _lines_from_overall_ocr_res(tess_data, SOURCE_TESSERACT)
        if lines:
            return lines

    # 4) parsing_res_list.
    if res_data:
        lines = _lines_from_parsing_res_list(res_data)
        if lines:
            return lines

        # 5) table_res_list[].pred_html.
        lines = _lines_from_table_res_list(res_data)
        if lines:
            return lines

    # 6) markdown file.
    markdown_path = _resolve_artifact(page.get("markdown"), output_dir)
    return _lines_from_markdown(markdown_path)


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
            lines = _extract_lines_with_fallback(page, output_dir, logger)

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
