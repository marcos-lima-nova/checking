"""Controlled extraction of .zip archives.

Strategy (from the spec):
  * Extract into a dedicated area under ``misc/extracted/<folder>/<zip_stem>/``.
  * Never mix extracted files with the original input files.
  * Avoid uncontrolled overwrite of previous extractions: an existing extraction
    directory is cleared before re-extracting.
  * Recursively walk extracted content, including internal subfolders.
  * Nested ``.zip`` archives are extracted recursively.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .exceptions import ExtractionError


@dataclass
class ExtractionResult:
    """Result of extracting one archive (including nested archives)."""

    zip_path: Path
    extract_dir: Path
    all_files: List[Path] = field(default_factory=list)


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract ``zf`` into ``dest`` guarding against Zip Slip path traversal."""
    dest = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        if not str(target).startswith(str(dest)):
            raise ExtractionError(
                f"Unsafe path in archive (zip slip blocked): {member}"
            )
    zf.extractall(dest)


def extract_zip(
    zip_path: Path,
    extracted_root: Path,
    folder_name: str,
    logger: logging.Logger,
    _seen: set | None = None,
) -> ExtractionResult:
    """Extract ``zip_path`` and recursively any nested archives.

    Args:
        zip_path: the ``.zip`` file to extract.
        extracted_root: ``misc/extracted`` root path.
        folder_name: name of the analyzed input subfolder (for path scoping).
        logger: folder logger.
        _seen: internal guard against cyclic/nested re-extraction.

    Returns:
        :class:`ExtractionResult` with the extraction directory and every
        extracted file (recursively, nested archives already expanded).

    Raises:
        ExtractionError: if the archive is invalid or unsafe.
    """
    zip_path = Path(zip_path)
    extracted_root = Path(extracted_root)
    _seen = _seen if _seen is not None else set()

    extract_dir = extracted_root / folder_name / zip_path.stem

    # Controlled overwrite: clear any prior extraction of this archive.
    if extract_dir.exists():
        logger.info("Clearing previous extraction directory: %s", extract_dir)
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting zip %s -> %s", zip_path, extract_dir)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            _safe_extract(zf, extract_dir)
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"Invalid zip archive: {zip_path} ({exc})") from exc

    result = ExtractionResult(zip_path=zip_path, extract_dir=extract_dir)

    # Collect every extracted file, expanding nested zips recursively.
    for path in sorted(extract_dir.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            key = str(path.resolve())
            if key in _seen:
                continue
            _seen.add(key)
            logger.info("Found nested zip: %s", path)
            try:
                nested = extract_zip(
                    path,
                    extracted_root,
                    f"{folder_name}/{zip_path.stem}",
                    logger,
                    _seen=_seen,
                )
                result.all_files.extend(nested.all_files)
            except ExtractionError as exc:
                logger.error("Failed to extract nested zip %s: %s", path, exc)
        else:
            result.all_files.append(path)

    logger.info(
        "Extracted %d file(s) from %s", len(result.all_files), zip_path.name
    )
    return result
