"""Crop exact PaddleOCR boxes out of a clean ``page_images/page_XXX.png``.

The base image must always be a clean render (see ``page_image_renderer.py``),
never a PaddleOCR diagnostic/annotated artifact. Coordinates come from
``rec_box`` (``[x1, y1, x2, y2]``); when the base image size does not match the
``*_res.json`` size, the caller-provided scale factors (from
``page_image_validator.py``) are applied before cropping.

Crop padding defaults to 0 (exact crop) and only takes effect when
``allow_padding_for_debug`` is enabled, per the spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from .config import TesseractConfig

STATUS_OK = "ok"
STATUS_FAILED = "failed"


@dataclass
class CropResult:
    box_index: int
    crop_path: Optional[str] = None  # relative to output_dir, if saved
    # PIL.Image.Image kept in-memory for the Tesseract runner; excluded from
    # repr/eq since it is only ever consumed in-process, never serialized.
    image: Optional[Any] = field(default=None, repr=False, compare=False)
    status: str = STATUS_FAILED
    error: Optional[str] = None


def _effective_padding(cfg: TesseractConfig) -> int:
    if cfg.allow_padding_for_debug:
        return max(0, cfg.crop_padding_px)
    return 0


def crop_boxes(
    page_image_path: Path,
    boxes: List[dict],
    mode: str,
    scale_x: float,
    scale_y: float,
    page_dir: Path,
    output_dir: Path,
    cfg: TesseractConfig,
    logger: logging.Logger,
) -> List[CropResult]:
    """Crop every box in ``boxes`` out of ``page_image_path``.

    Args:
        page_image_path: absolute path to the clean ``page_images/page_XXX.png``.
        boxes: list of box dicts from ``tesseract_box_extractor`` (each has
            ``box_index`` and ``rec_box``).
        mode: ``"direct"`` or ``"scaled"`` (from ``page_image_validator``); a
            page whose validation was ``"reject"`` must never reach this
            function.
        scale_x/scale_y: multipliers applied to ``rec_box`` coordinates when
            ``mode == "scaled"``.
        page_dir: absolute ``tesseract/page_XXX/`` directory for saved crops.
        output_dir: per-file output dir, used to compute relative crop paths.
        cfg: Tesseract configuration (padding/save flags).
        logger: folder logger.

    Returns:
        One :class:`CropResult` per input box, in the same order. Failures on
        individual boxes are isolated (status="failed") and do not raise.
    """
    from PIL import Image

    results: List[CropResult] = []

    try:
        base_image = Image.open(page_image_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - the whole page's crops fail together
        logger.error("box_cropper: failed to open base image %s: %s", page_image_path, exc)
        for box in boxes:
            results.append(
                CropResult(box_index=box.get("box_index", -1), status=STATUS_FAILED, error=str(exc))
            )
        return results

    padding = _effective_padding(cfg)
    img_w, img_h = base_image.size
    if cfg.save_box_crops:
        page_dir.mkdir(parents=True, exist_ok=True)

    for box in boxes:
        box_index = box.get("box_index", -1)
        rec_box = box.get("rec_box")
        try:
            if not rec_box or len(rec_box) != 4:
                raise ValueError(f"invalid rec_box: {rec_box!r}")

            x1, y1, x2, y2 = rec_box
            if mode == "scaled":
                x1, x2 = x1 * scale_x, x2 * scale_x
                y1, y2 = y1 * scale_y, y2 * scale_y

            x1 = int(round(x1)) - padding
            y1 = int(round(y1)) - padding
            x2 = int(round(x2)) + padding
            y2 = int(round(y2)) + padding

            # Clamp to image bounds and guard against degenerate boxes.
            x1 = max(0, min(x1, img_w - 1))
            y1 = max(0, min(y1, img_h - 1))
            x2 = max(x1 + 1, min(x2, img_w))
            y2 = max(y1 + 1, min(y2, img_h))

            crop = base_image.crop((x1, y1, x2, y2))

            result = CropResult(box_index=box_index, status=STATUS_OK)
            result.image = crop

            if cfg.save_box_crops:
                crop_path = page_dir / f"box_{box_index:03d}.png"
                crop.save(crop_path)
                result.crop_path = str(crop_path.relative_to(output_dir))

            results.append(result)
        except Exception as exc:  # noqa: BLE001 - isolate per-box failures
            logger.warning("box_cropper: failed to crop box %s: %s", box_index, exc)
            results.append(CropResult(box_index=box_index, status=STATUS_FAILED, error=str(exc)))

    return results
