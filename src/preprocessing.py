"""Preprocessing extension points (v1: no-ops).

This module intentionally contains only well-defined hooks. The first version
does NOT implement any PDF/image preprocessing (per the spec). These functions
exist so future stages (PDF->image conversion, deskew, binarization, contrast
enhancement, denoising, page selection, pipeline fallback) can be added without
touching the OCR runner or the orchestrator.

Each hook currently returns its input unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


def preprocess_input(
    path: Path,
    logger: Optional[logging.Logger] = None,
) -> Path:
    """Return a (possibly preprocessed) path to feed the OCR pipeline.

    v1: identity. Future implementations may return a new preprocessed file.
    """
    return path


# --- Individual future stages (documented no-ops) --------------------------- #

def pdf_to_images(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: convert PDF pages to images. v1: no-op."""
    return path


def deskew(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: correct skew. v1: no-op."""
    return path


def binarize(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: binarization. v1: no-op."""
    return path


def enhance_contrast(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: contrast enhancement. v1: no-op."""
    return path


def denoise(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: noise removal. v1: no-op."""
    return path


def resize(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: resizing. v1: no-op."""
    return path


def select_pages(path: Path, logger: Optional[logging.Logger] = None) -> Path:
    """Future: page selection. v1: no-op."""
    return path
