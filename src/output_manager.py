"""Output path management and skip/overwrite policy.

Layout (from the spec):
    output/<input_folder_name>/<file_stem>/     <- per-file native artifacts
    output/<input_folder_name>/processing_summary.json

Default behavior is to *skip* files whose output folder already exists and is
non-empty. This is configurable via ``skip_existing`` / ``overwrite_existing``.

Because the search inside a subfolder is recursive, two different files can share
the same stem (e.g. ``a/contrato.pdf`` and ``b/contrato.pdf``). To avoid one
overwriting the other, when a collision is detected we disambiguate the output
folder using a short suffix derived from the file's path relative to the input
folder.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional


def _sanitize_component(name: str) -> str:
    name = name.strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[\0<>:\"|?*]", "", name)
    return name or "file"


class OutputManager:
    """Resolves output folders and applies the skip/overwrite policy."""

    def __init__(
        self,
        output_root: Path,
        input_folder_name: str,
        skip_existing: bool = True,
        overwrite_existing: bool = False,
    ) -> None:
        self.output_root = Path(output_root)
        self.input_folder_name = input_folder_name
        self.skip_existing = skip_existing
        self.overwrite_existing = overwrite_existing
        # Track assigned output dirs to detect stem collisions within a folder.
        self._assigned: set[str] = set()

    @property
    def folder_output_dir(self) -> Path:
        """``output/<input_folder_name>/`` (kept as the original folder name)."""
        return self.output_root / self.input_folder_name

    def summary_path(self) -> Path:
        return self.folder_output_dir / "processing_summary.json"

    def resolve_output_dir(
        self,
        file_path: Path,
        input_folder: Path,
    ) -> Path:
        """Return the per-file output directory (``.../<file_stem>/``).

        On stem collision, append a suffix derived from the relative parent path
        so distinct source files never share an output folder.
        """
        stem = _sanitize_component(file_path.stem)
        candidate = self.folder_output_dir / stem

        if str(candidate) in self._assigned:
            # Build a disambiguating suffix from the file's relative location.
            try:
                rel_parent = file_path.parent.relative_to(input_folder)
            except ValueError:
                rel_parent = file_path.parent
            suffix = _sanitize_component("_".join(rel_parent.parts)) or "dup"
            candidate = self.folder_output_dir / f"{stem}__{suffix}"
            # Extremely rare secondary collision: add a counter.
            counter = 2
            while str(candidate) in self._assigned:
                candidate = self.folder_output_dir / f"{stem}__{suffix}_{counter}"
                counter += 1

        self._assigned.add(str(candidate))
        return candidate

    def should_process(self, output_dir: Path) -> bool:
        """Decide whether to process, honoring skip/overwrite.

        Returns True if OCR should run. If overwrite is enabled and the folder
        exists, it is cleared here so the new output is clean.
        """
        output_dir = Path(output_dir)
        exists_nonempty = output_dir.exists() and any(output_dir.iterdir())

        if not exists_nonempty:
            return True

        if self.overwrite_existing:
            shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            return True

        # skip_existing path
        return False
