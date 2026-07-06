"""Sanitized naming for pipeline-generated folders and files.

Every folder the segmentation stage creates must be sanitized (per
plan_opcao_B.md):

    NF 321        -> NF_321
    raw_NF 321    -> raw_NF_321
    Nota Fiscal   -> nota_fiscal   (document-type folders are lower-cased)

Rules implemented here:
  * ``sanitize_component`` preserves case and replaces spaces / illegal path
    characters with underscores. Used for file/raw stems.
  * ``sanitize_type`` additionally lower-cases, used for document-type folder
    names and identifiers coming from classification.
  * helpers build the concrete folder names used across the stage.

These are the single source of truth so naming stays consistent everywhere.
"""

from __future__ import annotations

import re

# Characters that are illegal or awkward in folder/file names across OSes.
_ILLEGAL = re.compile(r"[\0<>:\"|?*/\\]+")
_WS = re.compile(r"\s+")
_DASHES = re.compile(r"[-]+")
_MULTI_US = re.compile(r"_+")


def sanitize_component(name: str, *, fallback: str = "item") -> str:
    """Sanitize a path component, preserving case.

    Spaces and illegal characters collapse to single underscores. A leading/
    trailing underscore is trimmed. Returns ``fallback`` if nothing remains.
    """
    if name is None:
        return fallback
    text = str(name).strip()
    text = _ILLEGAL.sub("_", text)
    text = _WS.sub("_", text)
    text = _MULTI_US.sub("_", text).strip("_")
    return text or fallback


def sanitize_type(name: str, *, fallback: str = "unknown") -> str:
    """Sanitize + lower-case, for document-type folders/identifiers.

    Also turns hyphens into underscores so labels like "PI - Pedido" become
    ``pi_pedido`` style slugs.
    """
    if name is None:
        return fallback
    text = str(name).strip().lower()
    text = _ILLEGAL.sub("_", text)
    text = _DASHES.sub("_", text)
    text = _WS.sub("_", text)
    text = _MULTI_US.sub("_", text).strip("_")
    return text or fallback


def raw_folder_name(file_stem: str) -> str:
    """``raw_<sanitized_stem>`` folder name for the raw OCR artifacts."""
    return f"raw_{sanitize_component(file_stem, fallback='file')}"


def document_folder_name(document_id: str, document_type: str) -> str:
    """``NNN_tipo`` folder name for a logical document (type lower-cased)."""
    doc_id = sanitize_component(str(document_id), fallback="000")
    doc_type = sanitize_type(document_type, fallback="unknown")
    return f"{doc_id}_{doc_type}"


def page_folder_name(page_index: int) -> str:
    """``page_NNN`` folder name (zero-padded to 3 digits when possible)."""
    try:
        return f"page_{int(page_index):03d}"
    except (TypeError, ValueError):
        return f"page_{sanitize_component(str(page_index), fallback='unknown')}"
