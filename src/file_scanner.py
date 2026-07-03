"""Discovery of subfolders and supported files.

Rules enforced here (from the spec):
  * Only *subfolders* of ``inputs/checking`` are analyzed.
  * Files sitting directly inside ``inputs/checking`` are NEVER processed.
  * Inside each subfolder, the search for files is recursive.
  * ``.zip`` archives are identified separately for later extraction.
  * Unsupported files are collected (for logging) but not processed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .exceptions import TargetFolderNotFound


@dataclass
class ScanResult:
    """Outcome of scanning a single subfolder."""

    folder: Path
    supported_files: List[Path] = field(default_factory=list)
    zip_files: List[Path] = field(default_factory=list)
    unsupported_files: List[Path] = field(default_factory=list)

    @property
    def total_found(self) -> int:
        return (
            len(self.supported_files)
            + len(self.zip_files)
            + len(self.unsupported_files)
        )


def list_target_subfolders(
    input_root: Path,
    target_folder: Optional[str],
    process_all: bool,
) -> List[Path]:
    """Return the list of subfolders to process.

    * If ``target_folder`` is given, only that folder is returned (and it must
      exist, otherwise :class:`TargetFolderNotFound` is raised).
    * Otherwise, if ``process_all`` is True, every direct subfolder of
      ``input_root`` is returned.
    * Files directly inside ``input_root`` are ignored in all cases.
    """
    input_root = Path(input_root)

    if target_folder:
        candidate = input_root / target_folder
        if not candidate.exists() or not candidate.is_dir():
            raise TargetFolderNotFound(
                f"target_folder does not exist: {candidate}"
            )
        return [candidate]

    if not process_all:
        return []

    subfolders = sorted(
        (p for p in input_root.iterdir() if p.is_dir()),
        key=lambda p: p.name.lower(),
    )
    return subfolders


def scan_folder(folder: Path, accepted_extensions: List[str]) -> ScanResult:
    """Recursively scan ``folder`` and classify files.

    Args:
        folder: a subfolder of ``inputs/checking``.
        accepted_extensions: lower-case extensions with leading dot.

    Returns:
        A :class:`ScanResult` with supported files, zip archives, and
        unsupported files (the latter kept for logging only).
    """
    folder = Path(folder)
    accepted = {e.lower() for e in accepted_extensions}

    result = ScanResult(folder=folder)

    for path in sorted(folder.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext == ".zip":
            result.zip_files.append(path)
        elif ext in accepted:
            result.supported_files.append(path)
        else:
            result.unsupported_files.append(path)

    return result


def classify_extracted_files(
    files: List[Path],
    accepted_extensions: List[str],
):
    """Split an arbitrary list of files into (supported, zip, unsupported).

    Used for files coming out of an extracted archive, where the same accepted /
    unsupported rules apply and nested ``.zip`` files may appear.
    """
    accepted = {e.lower() for e in accepted_extensions}
    supported: List[Path] = []
    zips: List[Path] = []
    unsupported: List[Path] = []
    for path in files:
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext == ".zip":
            zips.append(path)
        elif ext in accepted:
            supported.append(path)
        else:
            unsupported.append(path)
    return supported, zips, unsupported
