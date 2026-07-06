"""Thin wrapper around Tesseract (via ``pytesseract``).

Every call into Tesseract lives here so the rest of the pipeline never talks
to ``pytesseract``/the ``tesseract`` binary directly. This isolation makes it
easy to adjust PSM/OEM/language or swap the OCR engine later.

Confidence handling: Tesseract reports confidence on a 0-100 scale (and -1 for
tokens it could not score). We normalize to 0.0-1.0 and treat any invalid value
(-1, ``None``, non-numeric) as ``0.0``.
"""

from __future__ import annotations

import logging
import shutil
from typing import Optional, Tuple

from .config import TesseractConfig

STATUS_RECOGNIZED = "recognized"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"

_availability_cache: dict = {}


def is_available(cfg: TesseractConfig) -> bool:
    """Check whether the configured Tesseract binary can be invoked.

    Cached per ``executable_path`` to avoid repeated subprocess probes.
    """
    key = cfg.executable_path
    if key in _availability_cache:
        return _availability_cache[key]

    available = False
    if shutil.which(cfg.executable_path) is not None:
        try:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = cfg.executable_path
            pytesseract.get_tesseract_version()
            available = True
        except Exception:  # noqa: BLE001 - any failure means "not available"
            available = False

    _availability_cache[key] = available
    return available


def _normalize_confidence(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value < 0:
        return 0.0
    return max(0.0, min(1.0, value / 100.0))


def ocr_crop(
    image,
    cfg: TesseractConfig,
    logger: Optional[logging.Logger] = None,
) -> Tuple[str, float, str]:
    """Run Tesseract on a single crop (PIL Image or numpy array).

    Returns ``(text, confidence_normalized, status)``. Never raises: any
    exception/timeout is converted into ``("", 0.0, "failed")`` and logged.
    """
    try:
        import pytesseract
        from pytesseract import Output

        pytesseract.pytesseract.tesseract_cmd = cfg.executable_path
        config_str = f"--psm {cfg.psm} --oem {cfg.oem}"
        data = pytesseract.image_to_data(
            image,
            lang=cfg.language,
            config=config_str,
            timeout=cfg.timeout_seconds_per_box,
            output_type=Output.DICT,
        )

        tokens = []
        confidences = []
        for text, conf in zip(data.get("text", []), data.get("conf", [])):
            text = (text or "").strip()
            if not text:
                continue
            tokens.append(text)
            confidences.append(_normalize_confidence(conf))

        full_text = " ".join(tokens).strip()
        if not full_text:
            return "", 0.0, STATUS_EMPTY

        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return full_text, confidence, STATUS_RECOGNIZED

    except Exception as exc:  # noqa: BLE001 - never break the caller
        if logger:
            logger.warning("tesseract_runner: OCR failed for a crop: %s", exc)
        return "", 0.0, STATUS_FAILED
