"""Render clean, per-page images used as the crop source for Tesseract.

These images (``page_images/page_XXX.png``) are rendered directly from the
file that was actually fed to PaddleOCR (``ocr_input``: the original PDF/image,
or the PDF produced by the office-document converter). They contain NO
PaddleOCR annotations, boxes, or diagnostic panels, which makes them the only
safe crop source for the Tesseract fusion stage.

Rendering strategy:
  * PDF: render each page (via PyMuPDF/``fitz``) at the exact
    ``width``/``height`` recorded in that page's ``*_res.json`` when available
    (so boxes never need scaling); falls back to ``render_dpi`` otherwise.
  * Single image (PNG/JPG/JPEG/TIFF/...): treated as ``page_000``; normalized
    (RGB) and copied into ``page_images/``.
  * DOC/DOCX/ODT: ``ocr_input`` is already the converted PDF, so this falls
    into the PDF branch automatically.

Per-page failures are isolated: a page that cannot be rendered is recorded with
``status="render_failed"`` and an ``error``, and other pages continue.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .config import PageImagesConfig
from .utils.path_utils import page_folder_name

STATUS_OK = "ok"
STATUS_RENDER_FAILED = "render_failed"

# Extensions PaddleOCR/the pipeline can send directly (no PDF conversion).
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


@dataclass
class PageImage:
    """Result of rendering one page's clean image."""

    page_index: object
    path: Optional[str] = None  # relative to the per-file output_dir
    width: int = 0
    height: int = 0
    json_width: int = 0
    json_height: int = 0
    scaled: bool = False
    status: str = STATUS_RENDER_FAILED
    error: Optional[str] = None


def _read_json_dims(res_json_path: Path) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Read ``width``, ``height``, ``input_path`` from a ``*_res.json`` file."""
    try:
        with res_json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("width"), data.get("height"), data.get("input_path")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None, None, None


def _dims_for_page(page_entry: Dict, output_dir: Path) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    res_rel = page_entry.get("res_json")
    if not res_rel:
        return None, None, None
    res_path = Path(res_rel)
    if not res_path.is_absolute():
        res_path = output_dir / res_path
    return _read_json_dims(res_path)


def _resolve_source(
    ocr_input: Optional[Path],
    inventory: Dict,
    output_dir: Path,
    logger: logging.Logger,
) -> Optional[Path]:
    """Resolve the file to render: prefer ``ocr_input``, else the JSON's ``input_path``.

    NEVER falls back to PaddleOCR diagnostic/annotated artifacts.
    """
    if ocr_input:
        candidate = Path(ocr_input)
        if candidate.exists():
            return candidate
        logger.warning("page_image_renderer: ocr_input not found: %s", candidate)

    for page_entry in inventory.get("pages", []):
        _, _, input_path = _dims_for_page(page_entry, output_dir)
        if input_path:
            candidate = Path(input_path)
            if candidate.exists():
                logger.info(
                    "page_image_renderer: falling back to _res.json input_path: %s",
                    candidate,
                )
                return candidate
    return None


def render_pages(
    ocr_input: Optional[Path],
    inventory: Dict,
    output_dir: Path,
    cfg: PageImagesConfig,
    logger: logging.Logger,
) -> Dict[object, PageImage]:
    """Render ``page_images/page_XXX.<ext>`` for every page in ``inventory``.

    Returns a mapping ``page_index -> PageImage``. Never raises; failures are
    reflected per page in the returned mapping.
    """
    output_dir = Path(output_dir)
    results: Dict[object, PageImage] = {}

    if not cfg.enabled:
        return results

    source = _resolve_source(ocr_input, inventory, output_dir, logger)
    if source is None:
        logger.error(
            "page_image_renderer: no usable source file found (ocr_input missing "
            "and no valid input_path in *_res.json)"
        )
        for page_entry in inventory.get("pages", []):
            page_index = page_entry.get("page_index")
            results[page_index] = PageImage(
                page_index=page_index, status=STATUS_RENDER_FAILED, error="no source file"
            )
        return results

    page_images_dir = output_dir / cfg.output_folder_name
    page_images_dir.mkdir(parents=True, exist_ok=True)
    ext = (cfg.image_format or "png").lower().lstrip(".")

    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return _render_pdf(source, inventory, output_dir, page_images_dir, cfg, ext, logger)
    if suffix in _IMAGE_EXTENSIONS:
        return _render_single_image(source, inventory, output_dir, page_images_dir, ext, logger)

    logger.error("page_image_renderer: unsupported source extension for render: %s", suffix)
    for page_entry in inventory.get("pages", []):
        page_index = page_entry.get("page_index")
        results[page_index] = PageImage(
            page_index=page_index,
            status=STATUS_RENDER_FAILED,
            error=f"unsupported source extension {suffix}",
        )
    return results


def _render_pdf(
    source: Path,
    inventory: Dict,
    output_dir: Path,
    page_images_dir: Path,
    cfg: PageImagesConfig,
    ext: str,
    logger: logging.Logger,
) -> Dict[object, PageImage]:
    import fitz  # PyMuPDF; imported lazily so importing this module is cheap.

    results: Dict[object, PageImage] = {}
    try:
        doc = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001 - isolate failures
        logger.error("page_image_renderer: failed to open PDF %s: %s", source, exc)
        for page_entry in inventory.get("pages", []):
            page_index = page_entry.get("page_index")
            results[page_index] = PageImage(
                page_index=page_index, status=STATUS_RENDER_FAILED, error=str(exc)
            )
        return results

    try:
        for page_entry in inventory.get("pages", []):
            page_index = page_entry.get("page_index")
            if not isinstance(page_index, int):
                results[page_index] = PageImage(
                    page_index=page_index,
                    status=STATUS_RENDER_FAILED,
                    error="non-integer page_index; cannot map to a PDF page",
                )
                continue
            try:
                if page_index < 0 or page_index >= doc.page_count:
                    raise ValueError(
                        f"page_index {page_index} out of range (pdf has {doc.page_count} page(s))"
                    )
                json_w, json_h, _ = _dims_for_page(page_entry, output_dir)
                pdf_page = doc[page_index]

                if json_w and json_h and cfg.validate_against_paddle_json_size:
                    zoom_x = json_w / pdf_page.rect.width
                    zoom_y = json_h / pdf_page.rect.height
                else:
                    zoom_x = zoom_y = cfg.render_dpi / 72.0

                matrix = fitz.Matrix(zoom_x, zoom_y)
                pix = pdf_page.get_pixmap(matrix=matrix, alpha=False)

                filename = f"{page_folder_name(page_index)}.{ext}"
                out_path = page_images_dir / filename
                pix.save(str(out_path))

                scaled = bool(json_w and json_h and (pix.width != json_w or pix.height != json_h))
                results[page_index] = PageImage(
                    page_index=page_index,
                    path=str(out_path.relative_to(output_dir)),
                    width=pix.width,
                    height=pix.height,
                    json_width=json_w or 0,
                    json_height=json_h or 0,
                    scaled=scaled,
                    status=STATUS_OK,
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-page failures
                logger.error(
                    "page_image_renderer: failed rendering page %s: %s", page_index, exc
                )
                results[page_index] = PageImage(
                    page_index=page_index, status=STATUS_RENDER_FAILED, error=str(exc)
                )
    finally:
        doc.close()

    return results


def _render_single_image(
    source: Path,
    inventory: Dict,
    output_dir: Path,
    page_images_dir: Path,
    ext: str,
    logger: logging.Logger,
) -> Dict[object, PageImage]:
    from PIL import Image

    results: Dict[object, PageImage] = {}
    pages = inventory.get("pages", [])
    if not pages:
        return results

    # A direct image source produces exactly one page; treat the first
    # inventory entry as that page (its page_index is usually 0).
    primary_entry = pages[0]
    page_index = primary_entry.get("page_index", 0)
    try:
        with Image.open(source) as img:
            img = img.convert("RGB")
            filename = f"{page_folder_name(page_index)}.{ext}"
            out_path = page_images_dir / filename
            img.save(out_path)
            results[page_index] = PageImage(
                page_index=page_index,
                path=str(out_path.relative_to(output_dir)),
                width=img.width,
                height=img.height,
                status=STATUS_OK,
            )
    except Exception as exc:  # noqa: BLE001 - isolate failures
        logger.error("page_image_renderer: failed to normalize image %s: %s", source, exc)
        results[page_index] = PageImage(
            page_index=page_index, status=STATUS_RENDER_FAILED, error=str(exc)
        )

    for extra_entry in pages[1:]:
        extra_index = extra_entry.get("page_index")
        results[extra_index] = PageImage(
            page_index=extra_index,
            status=STATUS_RENDER_FAILED,
            error="unexpected additional page for a single-image source",
        )

    return results
