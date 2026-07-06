"""Build an inventory of raw PaddleOCR artifacts, grouped by page.

After OCR, native artifacts land in ``output/<folder>/<stem>/raw_<stem>/`` with
names like ``<stem>_<page_index><suffix>`` (e.g. ``NF 321_0_res.json``,
``NF 321_0.md``, ``NF 321_0_table_1.html``). This module scans that raw folder,
locates every ``*_res.json``, determines each page's index, and associates the
sibling artifacts belonging to the same page.

Page-index resolution is robust (per plan Etapa 2):
  1. prefer the ``page_index`` field inside the JSON;
  2. otherwise infer from the filename pattern ``_<N>_res.json`` / ``_<N>.md``;
  3. otherwise mark ``unknown_page_index`` and log a warning.

Output: ``page_inventory.json`` plus an in-memory structure reused downstream.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

# Recognized artifact suffixes -> the inventory key we expose them under.
# The value is a description; the matching is by suffix on the file name.
_RES_JSON_RE = re.compile(r"_(\d+)_res\.json$", re.IGNORECASE)
_INDEX_TAIL_RE = re.compile(r"_(\d+)(?:_[a-z0-9_]+)?\.[A-Za-z0-9]+$")

# Single-artifact suffixes (one file per page).
_SINGLE_SUFFIXES = {
    "res_json": "_res.json",
    "markdown": ".md",
    "docx": ".docx",
    "tex": ".tex",
    "layout_image": "_layout_det_res.png",
    "layout_order_image": "_layout_order_res.png",
    "overall_ocr_image": "_overall_ocr_res.png",
    "region_det_image": "_region_det_res.png",
    "preprocessed_image": "_preprocessed_img.png",
}


def _page_index_from_name(name: str) -> Optional[int]:
    """Infer the page index from an artifact filename, or None."""
    m = _RES_JSON_RE.search(name)
    if m:
        return int(m.group(1))
    m = _INDEX_TAIL_RE.search(name)
    if m:
        return int(m.group(1))
    return None


def _read_json_page_index(res_json: Path) -> Optional[int]:
    """Read the ``page_index`` field from a ``_res.json`` file, or None."""
    try:
        with res_json.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        pi = data.get("page_index")
        if isinstance(pi, int):
            return pi
        if isinstance(pi, str) and pi.isdigit():
            return int(pi)
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return None


def _match_page_index(res_json: Path, logger: logging.Logger) -> object:
    """Resolve a page index, following the robust 3-step strategy.

    Returns an int page index, or the string ``"unknown_page_index"``.
    """
    pi = _read_json_page_index(res_json)
    if pi is not None:
        return pi
    pi = _page_index_from_name(res_json.name)
    if pi is not None:
        logger.debug("page_index for %s inferred from filename: %s", res_json.name, pi)
        return pi
    logger.warning(
        "Could not determine page_index for %s; marking unknown_page_index",
        res_json.name,
    )
    return "unknown_page_index"


def build_inventory(
    raw_dir: Path,
    source_file: Path,
    output_dir: Path,
    logger: logging.Logger,
) -> Dict:
    """Scan ``raw_dir`` and build the page inventory.

    Args:
        raw_dir: the ``raw_<stem>`` folder holding native OCR artifacts.
        source_file: the original input file (for provenance in the JSON).
        output_dir: the per-file output dir (where page_inventory.json is written).
        logger: folder logger.

    Returns:
        The inventory dict (also written to ``output_dir/page_inventory.json``).
    """
    raw_dir = Path(raw_dir)
    output_dir = Path(output_dir)

    files = sorted((p for p in raw_dir.iterdir() if p.is_file()), key=lambda p: p.name.lower()) if raw_dir.exists() else []

    res_jsons = [p for p in files if p.name.lower().endswith("_res.json")]
    if not res_jsons:
        logger.warning("No *_res.json found in raw dir: %s", raw_dir)

    pages: List[Dict] = []
    for res_json in res_jsons:
        page_index = _match_page_index(res_json, logger)
        # The stem prefix for this page, e.g. "NF 321_0" for "NF 321_0_res.json".
        prefix = res_json.name[: -len("_res.json")]

        artifacts: Dict[str, object] = {}
        table_files: List[str] = []
        image_files: List[str] = []

        for f in files:
            if not f.name.startswith(prefix):
                continue
            rel = str(f.relative_to(output_dir)) if _is_relative(f, output_dir) else str(f)
            tail = f.name[len(prefix):]
            matched_single = False
            for key, suffix in _SINGLE_SUFFIXES.items():
                if tail.lower() == suffix.lower():
                    artifacts[key] = rel
                    matched_single = True
                    break
            if matched_single:
                continue
            # Tables: <prefix>_table_<n>.html / .xlsx
            low = f.name.lower()
            if "_table_" in low and (low.endswith(".html") or low.endswith(".xlsx")):
                table_files.append(rel)
            elif low.endswith(".png") or low.endswith(".jpg") or low.endswith(".jpeg"):
                image_files.append(rel)

        page = {
            "page_index": page_index,
            "artifact_prefix": prefix,
            "res_json": artifacts.get("res_json"),
            "markdown": artifacts.get("markdown"),
            "docx": artifacts.get("docx"),
            "tex": artifacts.get("tex"),
            "layout_image": artifacts.get("layout_image"),
            "layout_order_image": artifacts.get("layout_order_image"),
            "overall_ocr_image": artifacts.get("overall_ocr_image"),
            "region_det_image": artifacts.get("region_det_image"),
            "preprocessed_image": artifacts.get("preprocessed_image"),
            "table_files": sorted(table_files),
            "extra_images": sorted(image_files),
        }
        pages.append(page)

    # Sort by page index (unknown pages go last, preserving discovery order).
    pages.sort(key=lambda p: (p["page_index"] == "unknown_page_index", _sort_key(p["page_index"])))

    inventory = {
        "source_file": str(source_file),
        "source_file_name": Path(source_file).name,
        "raw_output_folder": _rel_or_abs(raw_dir, output_dir),
        "total_pages": len(pages),
        "pages": pages,
    }

    out_path = output_dir / "page_inventory.json"
    _write_json(out_path, inventory)
    logger.info("Wrote page_inventory.json (%d page(s)) -> %s", len(pages), out_path)
    return inventory


def _sort_key(page_index) -> int:
    return page_index if isinstance(page_index, int) else 1_000_000


def _is_relative(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _rel_or_abs(path: Path, base: Path) -> str:
    return str(path.relative_to(base)) if _is_relative(path, base) else str(path)


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
