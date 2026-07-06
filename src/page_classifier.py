"""Rule-based per-page classification.

For each page in the text index, score every configured document type by
counting matched strong/weak terms in the page's normalized text, then pick the
best type and apply thresholds:

  * confidence >= thresholds.classified   -> status "classified"
  * confidence >= thresholds.needs_review -> status "needs_review"
  * confidence <  thresholds.unknown_below-> status "unknown"

Scoring (v1, intentionally simple and explainable):
  points = (#strong matched * strong_weight) + (#weak matched * weak_weight)
  confidence = points / max_points_for_that_type

Per-page failures are isolated: a page that raises during scoring is recorded
as ``unknown`` with an ``error`` field, and processing continues.

Output: ``page_classification.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from .document_rules import DocumentRules, DocumentType

UNKNOWN_TYPE = "unknown"

STATUS_CLASSIFIED = "classified"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_UNKNOWN = "unknown"


def _matched_terms(normalized_text: str, terms: List[str]) -> List[str]:
    """Return which normalized terms appear as substrings of the page text."""
    return [t for t in terms if t and t in normalized_text]


def _score_type(
    normalized_text: str,
    dtype: DocumentType,
    rules: DocumentRules,
) -> Tuple[float, List[str]]:
    """Return (confidence, matched_terms) for one document type."""
    strong_hits = _matched_terms(normalized_text, dtype.strong_terms)
    weak_hits = _matched_terms(normalized_text, dtype.weak_terms)
    points = len(strong_hits) * rules.strong_weight + len(weak_hits) * rules.weak_weight
    max_points = dtype.max_points(rules.strong_weight, rules.weak_weight)
    confidence = (points / max_points) if max_points > 0 else 0.0
    return round(confidence, 4), strong_hits + weak_hits


def classify_page(normalized_text: str, rules: DocumentRules) -> Dict:
    """Classify a single page's normalized text; returns a classification dict."""
    best_type = UNKNOWN_TYPE
    best_conf = 0.0
    best_matched: List[str] = []

    for key, dtype in rules.types.items():
        conf, matched = _score_type(normalized_text, dtype, rules)
        if conf > best_conf:
            best_conf = conf
            best_type = key
            best_matched = matched

    th = rules.thresholds
    if best_conf >= th.classified:
        status = STATUS_CLASSIFIED
        needs_review = False
        doc_type = best_type
    elif best_conf >= th.needs_review:
        status = STATUS_NEEDS_REVIEW
        needs_review = True
        doc_type = best_type
    else:
        status = STATUS_UNKNOWN
        needs_review = True
        doc_type = UNKNOWN_TYPE

    return {
        "document_type": doc_type,
        "confidence": best_conf,
        "status": status,
        "method": "rules",
        "matched_terms": best_matched,
        "needs_review": needs_review,
        # Retained so the segmenter can evaluate continuation compatibility.
        "best_candidate_type": best_type,
    }


def classify_pages(
    text_index: Dict,
    rules: DocumentRules,
    output_dir: Path,
    logger: logging.Logger,
) -> Dict:
    """Classify every page in the text index.

    Returns the classification dict (also written to page_classification.json).
    """
    output_dir = Path(output_dir)
    pages_out: List[Dict] = []

    for page in text_index.get("pages", []):
        page_index = page.get("page_index")
        normalized_text = page.get("normalized_text", "") or ""
        try:
            result = classify_page(normalized_text, rules)
        except Exception as exc:  # noqa: BLE001 - isolate per-page failures
            logger.error("Classification failed for page %s: %s", page_index, exc)
            result = {
                "document_type": UNKNOWN_TYPE,
                "confidence": 0.0,
                "status": STATUS_UNKNOWN,
                "method": "rules",
                "matched_terms": [],
                "needs_review": True,
                "best_candidate_type": UNKNOWN_TYPE,
                "error": f"{type(exc).__name__}: {exc}",
            }
        result["page_index"] = page_index
        pages_out.append(result)
        logger.info(
            "Page %s classified as %s (conf=%.2f, status=%s)",
            page_index,
            result["document_type"],
            result["confidence"],
            result["status"],
        )

    classification = {
        "source_file": text_index.get("source_file"),
        "thresholds": {
            "classified": rules.thresholds.classified,
            "needs_review": rules.thresholds.needs_review,
            "unknown_below": rules.thresholds.unknown_below,
            "continuation_min_confidence": rules.thresholds.continuation_min_confidence,
        },
        "pages": pages_out,
    }

    out_path = output_dir / "page_classification.json"
    _write_json(out_path, classification)
    logger.info("Wrote page_classification.json (%d page(s)) -> %s", len(pages_out), out_path)
    return classification


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
