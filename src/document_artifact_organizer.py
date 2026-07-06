"""Copy per-page artifacts into the logical-document folder layout.

Given the document groups and the page inventory, this builds:

    documents/<NNN_tipo>/pages/page_<NNN>/<artifacts>

copying (never moving - acceptance criterion 11) at least:
  * ``*_res.json``
  * ``*.md``
  * ``*_layout_det_res.png``
  * ``*_overall_ocr_res.png``
  * tables ``*.html`` / ``*.xlsx`` when present
  * ``*.docx`` when present

The raw folder is left completely intact. Returns a per-document mapping of the
copied artifacts (relative to the document folder) so downstream writers can
reference them without re-scanning the disk.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .utils.path_utils import document_folder_name, page_folder_name

# Keys from the inventory we copy for each page (single-file artifacts).
_COPY_KEYS = [
    "res_json",
    "markdown",
    "docx",
    "layout_image",
    "overall_ocr_image",
]


def _resolve(rel_or_abs: Optional[str], output_dir: Path) -> Optional[Path]:
    if not rel_or_abs:
        return None
    p = Path(rel_or_abs)
    return p if p.is_absolute() else output_dir / p


def _index_pages_by_index(inventory: Dict) -> Dict:
    """Map page_index -> inventory page entry for quick lookup."""
    mapping: Dict = {}
    for page in inventory.get("pages", []):
        mapping[page.get("page_index")] = page
    return mapping


def _copy_file(src: Optional[Path], dest_dir: Path, logger: logging.Logger) -> Optional[str]:
    """Copy ``src`` into ``dest_dir`` (preserving name). Returns dest file name."""
    if src is None or not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    try:
        shutil.copy2(src, dest)
        return dest.name
    except OSError as exc:
        logger.error("Failed to copy artifact %s -> %s: %s", src, dest, exc)
        return None


def organize_documents(
    groups: Dict,
    inventory: Dict,
    output_dir: Path,
    documents_dir: Path,
    logger: logging.Logger,
) -> Dict[str, Dict]:
    """Create documents/<NNN_tipo>/pages/page_NNN/ and copy artifacts.

    Returns a mapping ``document_id -> {"folder": <rel path>, "pages": {...}}``
    describing what was copied, for use by the metadata/llm_ready writers.
    """
    output_dir = Path(output_dir)
    documents_dir = Path(documents_dir)
    documents_dir.mkdir(parents=True, exist_ok=True)

    pages_by_index = _index_pages_by_index(inventory)
    result: Dict[str, Dict] = {}

    for doc in groups.get("documents", []):
        doc_id = doc.get("document_id", "000")
        doc_type = doc.get("document_type", "unknown")
        folder_name = document_folder_name(doc_id, doc_type)
        doc_folder = documents_dir / folder_name
        pages_root = doc_folder / "pages"
        pages_root.mkdir(parents=True, exist_ok=True)

        page_artifacts: Dict = {}
        for page_index in doc.get("pages", []):
            inv_page = pages_by_index.get(page_index)
            page_dir = pages_root / page_folder_name(page_index)

            copied: Dict[str, object] = {}
            if inv_page is None:
                logger.warning(
                    "No inventory entry for page %s in document %s; skipping copy",
                    page_index,
                    doc_id,
                )
            else:
                for key in _COPY_KEYS:
                    name = _copy_file(_resolve(inv_page.get(key), output_dir), page_dir, logger)
                    if name:
                        copied[key] = str(Path("pages") / page_folder_name(page_index) / name)
                # Tables (list).
                table_names: List[str] = []
                for tbl in inv_page.get("table_files", []) or []:
                    name = _copy_file(_resolve(tbl, output_dir), page_dir, logger)
                    if name:
                        table_names.append(
                            str(Path("pages") / page_folder_name(page_index) / name)
                        )
                copied["table_files"] = table_names

            page_artifacts[page_index] = copied

        result[doc_id] = {
            "folder": str(doc_folder.relative_to(output_dir)),
            "folder_name": folder_name,
            "pages": page_artifacts,
        }
        logger.info(
            "Organized document %s (%s): %d page(s) -> %s",
            doc_id,
            doc_type,
            len(doc.get("pages", [])),
            doc_folder,
        )

    return result
