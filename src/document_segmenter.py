"""Group classified pages into logical documents.

Reads ``page_classification.json`` and produces ``document_groups.json`` by:

  * applying the continuation rule (plan Etapa 6): a low-confidence page that
    follows a classified document, has no strong evidence of another type, has
    weak-term compatibility with the previous type, and scores at least
    ``continuation_min_confidence``, is attached to the previous document as a
    continuation (which marks the whole document ``needs_review``);
  * grouping consecutive same-type pages into one document;
  * starting a new document when the type changes (so type A -> B -> A yields
    three documents);
  * treating ``unknown`` pages as their own documents.

The minimal unit is the page; there is no intra-page splitting in this version.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .document_rules import DocumentRules
from .page_classifier import UNKNOWN_TYPE

STATUS_CLASSIFIED = "classified"
STATUS_UNKNOWN = "unknown"
STATUS_WITH_CONTINUATION = "classified_with_continuation"
STATUS_NEEDS_REVIEW = "needs_review"

CONTINUATION_STATUS = "continuation"
CONTINUATION_METHOD = "rules_continuation"


def _is_classified_type(doc_type: str) -> bool:
    return bool(doc_type) and doc_type != UNKNOWN_TYPE


def _weak_terms_for(rules: DocumentRules, doc_type: str) -> set:
    dtype = rules.types.get(doc_type)
    return set(dtype.weak_terms) if dtype else set()


def _strong_terms_for(rules: DocumentRules, doc_type: str) -> set:
    dtype = rules.types.get(doc_type)
    return set(dtype.strong_terms) if dtype else set()


def _looks_like_continuation(
    page: Dict,
    prev_type: str,
    rules: DocumentRules,
) -> bool:
    """Decide whether ``page`` should attach to the previous document.

    Conditions (all must hold):
      * there is a previous classified document;
      * the page's own confidence is below the classified threshold but at least
        continuation_min_confidence;
      * no strong evidence for a different type;
      * weak-term compatibility with the previous type (at least one weak match,
        or no competing strong matches at all).
    """
    if not _is_classified_type(prev_type):
        return False

    conf = page.get("confidence", 0.0) or 0.0
    th = rules.thresholds
    if conf >= th.classified:
        return False
    if conf < th.continuation_min_confidence:
        return False

    matched = set(page.get("matched_terms", []) or [])

    # Strong evidence of a DIFFERENT type disqualifies continuation.
    for other_type in rules.types:
        if other_type == prev_type:
            continue
        if matched & _strong_terms_for(rules, other_type):
            return False

    # Weak-term compatibility with the previous type: at least one weak term of
    # the previous type present, OR the page carries no strong term of any type
    # (i.e. it is a "spillover" page with only generic/weak content).
    prev_weak = _weak_terms_for(rules, prev_type)
    prev_strong = _strong_terms_for(rules, prev_type)
    if matched & prev_weak:
        return True
    any_strong = any(matched & _strong_terms_for(rules, t) for t in rules.types)
    if not any_strong or (matched & prev_strong):
        return True
    return False


def segment_documents(
    classification: Dict,
    rules: DocumentRules,
    output_dir: Path,
    logger: logging.Logger,
) -> Dict:
    """Group classified pages into logical documents.

    Returns the groups dict (also written to ``document_groups.json``).
    """
    output_dir = Path(output_dir)
    pages: List[Dict] = list(classification.get("pages", []))

    documents: List[Dict] = []
    current: Optional[Dict] = None
    seq = 0

    def _finalize_current() -> None:
        nonlocal current
        if current is not None:
            documents.append(current)
            current = None

    for page in pages:
        page_index = page.get("page_index")
        doc_type = page.get("document_type", UNKNOWN_TYPE)
        status = page.get("status", STATUS_UNKNOWN)
        needs_review = bool(page.get("needs_review", False))
        prev_type = current["document_type"] if current else None

        # 1) Continuation: attach a weak page to the previous classified document.
        if current is not None and _looks_like_continuation(page, prev_type, rules):
            current["pages"].append(page_index)
            current["needs_review"] = True
            current["status"] = STATUS_WITH_CONTINUATION
            current.setdefault("continuation_pages", []).append(page_index)
            # Annotate the page record for downstream artifacts.
            page["status"] = CONTINUATION_STATUS
            page["method"] = CONTINUATION_METHOD
            page["attached_to_previous_document"] = True
            page["needs_review"] = True
            page.setdefault(
                "reason",
                "Pagina com baixa confianca propria, mas compativel com o tipo da pagina anterior.",
            )
            logger.info(
                "Page %s attached as continuation of document %s (%s)",
                page_index,
                current["document_id"],
                prev_type,
            )
            continue

        # 2) unknown pages become their own document.
        if doc_type == UNKNOWN_TYPE:
            _finalize_current()
            seq += 1
            documents.append(
                {
                    "document_id": f"{seq:03d}",
                    "document_type": UNKNOWN_TYPE,
                    "pages": [page_index],
                    "status": STATUS_UNKNOWN,
                    "needs_review": True,
                    "confidence": page.get("confidence", 0.0),
                }
            )
            continue

        # 3) same type as current -> extend; else start a new document.
        if current is not None and current["document_type"] == doc_type:
            current["pages"].append(page_index)
            if needs_review:
                current["needs_review"] = True
            # Track the strongest confidence seen for the group.
            current["confidence"] = max(current["confidence"], page.get("confidence", 0.0))
        else:
            _finalize_current()
            seq += 1
            current = {
                "document_id": f"{seq:03d}",
                "document_type": doc_type,
                "pages": [page_index],
                "status": STATUS_CLASSIFIED if status == STATUS_CLASSIFIED else STATUS_NEEDS_REVIEW,
                "needs_review": needs_review,
                "confidence": page.get("confidence", 0.0),
            }

    _finalize_current()

    # Round confidences for stable JSON.
    for doc in documents:
        doc["confidence"] = round(float(doc.get("confidence", 0.0)), 4)

    groups = {
        "source_file": classification.get("source_file"),
        "total_documents": len(documents),
        "documents": documents,
    }

    out_path = output_dir / "document_groups.json"
    _write_json(out_path, groups)
    logger.info("Wrote document_groups.json (%d document(s)) -> %s", len(documents), out_path)
    return groups


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
