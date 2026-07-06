"""Validate that a candidate crop-source image matches the PaddleOCR JSON.

Before cropping boxes out of an image, we must confirm the image is a clean
render whose pixel coordinates match ``overall_ocr_res.rec_boxes`` /
``rec_polys`` from the corresponding ``*_res.json``. Three outcomes:

  * ``direct``  - image size equals the JSON width/height; use boxes as-is.
  * ``scaled``  - image size differs but is plausible; scale boxes by the
    width/height ratio.
  * ``reject``  - the image looks like a PaddleOCR diagnostic panel (e.g. the
    triple Original/Rotated/Unwarping preview in ``*_preprocessed_img.png``),
    which is never an acceptable crop source.

This module has no I/O; callers pass in the already-known dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

MODE_DIRECT = "direct"
MODE_SCALED = "scaled"
MODE_REJECT = "reject"

REJECT_MESSAGE = (
    "Imagem-base inválida para crop: parece ser painel de diagnóstico do "
    "PaddleOCR. Use page_images/page_XXX.png."
)


@dataclass
class ValidationResult:
    mode: str
    scale_x: float = 1.0
    scale_y: float = 1.0
    reason: Optional[str] = None


def validate_base_image(
    json_width: int,
    json_height: int,
    image_width: int,
    image_height: int,
    *,
    suspicious_panel_ratio: float = 2.5,
    allow_box_scaling: bool = True,
) -> ValidationResult:
    """Decide how (or whether) to use ``image_width``x``image_height`` for crops.

    Args:
        json_width/json_height: dimensions recorded in the PaddleOCR ``*_res.json``.
        image_width/image_height: dimensions of the candidate base image.
        suspicious_panel_ratio: an image whose width is >= ``json_width`` times
            this ratio is rejected as a likely diagnostic panel.
        allow_box_scaling: if False, any size mismatch is rejected instead of
            scaled.

    Returns:
        A :class:`ValidationResult` with ``mode`` and, for ``scaled``, the
        per-axis scale factors to apply to ``rec_box``/``rec_poly`` coordinates.
    """
    if not json_width or not json_height:
        return ValidationResult(
            mode=MODE_REJECT,
            reason="_res.json has no valid width/height to validate against",
        )
    if not image_width or not image_height:
        return ValidationResult(mode=MODE_REJECT, reason="base image has no valid dimensions")

    if image_width == json_width and image_height == json_height:
        return ValidationResult(mode=MODE_DIRECT, scale_x=1.0, scale_y=1.0)

    if image_width >= json_width * suspicious_panel_ratio:
        return ValidationResult(mode=MODE_REJECT, reason=REJECT_MESSAGE)

    if not allow_box_scaling:
        return ValidationResult(
            mode=MODE_REJECT,
            reason=(
                f"size mismatch (image {image_width}x{image_height} vs json "
                f"{json_width}x{json_height}) and box scaling is disabled"
            ),
        )

    scale_x = image_width / json_width
    scale_y = image_height / json_height
    return ValidationResult(mode=MODE_SCALED, scale_x=scale_x, scale_y=scale_y)
