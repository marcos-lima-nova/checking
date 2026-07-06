"""Write per-document ``document_metadata.json`` and ``llm_ready.json``.

For each logical document, this produces two files inside the document folder:

  * ``document_metadata.json`` - compact metadata for indexing/triage;
  * ``llm_ready.json`` - the self-contained payload for the next LLM stage,
    including per-page text lines, low-confidence lines, artifact references
    (relative to the document folder) and explicit instructions.

Text content is pulled from the page text index so the LLM does not need to
re-parse the raw OCR JSON.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

# Instructions handed to the downstream LLM (plan Etapa 10).
DEFAULT_LLM_INSTRUCTIONS: List[str] = [
    "Analise apenas este documento logico.",
    "Nao misture dados com outros documentos detectados no mesmo arquivo original.",
    "Use o JSON como fonte principal.",
    "Use as imagens de layout e OCR como evidencia visual.",
    "Se houver divergencia, baixa confianca ou campo ilegivel, marque como needs_review.",
    "Nao invente valores ausentes.",
]


def page_range_str(pages: List) -> str:
    """Human-readable 1-based page range, e.g. [0] -> "1", [1,2] -> "2-3"."""
    numeric = [p for p in pages if isinstance(p, int)]
    if not numeric:
        return ""
    numeric = sorted(numeric)
    # Contiguous check for a compact "a-b"; otherwise comma-list of 1-based nums.
    contiguous = all(numeric[i] + 1 == numeric[i + 1] for i in range(len(numeric) - 1))
    if contiguous:
        if len(numeric) == 1:
            return str(numeric[0] + 1)
        return f"{numeric[0] + 1}-{numeric[-1] + 1}"
    return ", ".join(str(n + 1) for n in numeric)


def _index_text_pages(text_index: Dict) -> Dict:
    return {p.get("page_index"): p for p in text_index.get("pages", [])}


def _document_label(rules_types: Dict, doc_type: str) -> str:
    dtype = rules_types.get(doc_type) if rules_types else None
    return dtype.label if dtype is not None else doc_type


def write_document_files(
    groups: Dict,
    organized: Dict[str, Dict],
    text_index: Dict,
    classification: Dict,
    rules_types: Dict,
    source_file: Path,
    output_dir: Path,
    output_folder_display: str,
    logger: logging.Logger,
    instructions: Optional[List[str]] = None,
) -> List[Dict]:
    """Write metadata + llm_ready for every document; return per-doc summaries.

    The returned list feeds the manifest writer and the processing summary.
    """
    output_dir = Path(output_dir)
    source_file = Path(source_file)
    text_pages = _index_text_pages(text_index)
    matched_by_page = _index_matched_terms(classification)
    instructions = instructions or DEFAULT_LLM_INSTRUCTIONS

    summaries: List[Dict] = []

    for doc in groups.get("documents", []):
        doc_id = doc.get("document_id", "000")
        doc_type = doc.get("document_type", "unknown")
        org = organized.get(doc_id, {})
        folder_rel = org.get("folder")
        if not folder_rel:
            logger.warning("Document %s has no organized folder; skipping writers", doc_id)
            continue
        doc_folder = output_dir / folder_rel
        doc_folder.mkdir(parents=True, exist_ok=True)

        label = _document_label(rules_types, doc_type)
        pages = doc.get("pages", [])
        prange = page_range_str(pages)
        status = doc.get("status", "unknown")
        needs_review = bool(doc.get("needs_review", False))
        confidence = doc.get("confidence", 0.0)

        # --- document_metadata.json ---
        metadata = {
            "document_id": doc_id,
            "document_type": doc_type,
            "document_label": label,
            "source_file": str(source_file),
            "source_file_name": source_file.name,
            "source_pages": pages,
            "page_range": prange,
            "status": status,
            "needs_review": needs_review,
            "confidence": confidence,
            "classification_method": "rules",
            "output_folder": str(Path(output_folder_display) / folder_rel),
        }
        _write_json(doc_folder / "document_metadata.json", metadata)

        # --- llm_ready.json ---
        llm_pages: List[Dict] = []
        page_art = org.get("pages", {})
        for page_index in pages:
            tp = text_pages.get(page_index, {})
            arts = page_art.get(page_index, {})
            llm_pages.append(
                {
                    "page_index": page_index,
                    "page_number": (page_index + 1) if isinstance(page_index, int) else None,
                    "text_lines": tp.get("raw_text_lines", []),
                    "low_confidence_lines": tp.get("low_confidence_lines", []),
                    "artifacts": {
                        "res_json": arts.get("res_json"),
                        "markdown": arts.get("markdown"),
                        "layout_image": arts.get("layout_image"),
                        "overall_ocr_image": arts.get("overall_ocr_image"),
                        "table_files": arts.get("table_files", []),
                    },
                }
            )

        llm_ready = {
            "document_id": doc_id,
            "document_type": doc_type,
            "document_label": label,
            "source_file": source_file.name,
            "source_pages": pages,
            "classification": {
                "method": "rules",
                "status": status,
                "confidence": confidence,
                "needs_review": needs_review,
                "matched_terms": _collect_matched_terms(matched_by_page, pages),
            },
            "pages": llm_pages,
            "instructions_for_llm": instructions,
        }
        _write_json(doc_folder / "llm_ready.json", llm_ready)

        summaries.append(
            {
                "document_id": doc_id,
                "document_type": doc_type,
                "document_label": label,
                "pages": pages,
                "page_range": prange,
                "confidence": confidence,
                "status": status,
                "needs_review": needs_review,
                "output_folder": str(Path(output_folder_display) / folder_rel),
                "document_metadata": str(Path(output_folder_display) / folder_rel / "document_metadata.json"),
                "llm_ready": str(Path(output_folder_display) / folder_rel / "llm_ready.json"),
            }
        )
        logger.info("Wrote metadata + llm_ready for document %s (%s)", doc_id, doc_type)

    return summaries


def _index_matched_terms(classification: Dict) -> Dict:
    """Map page_index -> matched_terms list from the classification result."""
    out: Dict = {}
    for page in (classification or {}).get("pages", []):
        out[page.get("page_index")] = page.get("matched_terms", []) or []
    return out


def _collect_matched_terms(matched_by_page: Dict, pages: List) -> List[str]:
    """Union of matched terms across a document's pages (order-preserving)."""
    seen = set()
    out: List[str] = []
    for page_index in pages:
        for term in matched_by_page.get(page_index, []):
            if term not in seen:
                seen.add(term)
                out.append(term)
    return out


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
