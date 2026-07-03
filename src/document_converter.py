"""Document conversion: .doc / .docx / .odt -> .pdf.

PaddleOCR is not fed these formats directly. Instead we convert them to PDF in
an isolated step, then OCR the resulting PDF.

The conversion strategy is isolated behind :class:`DocumentConverter` so it can
be swapped later (e.g. for a different office suite or a service). The default
strategy uses LibreOffice in headless mode.

Converted PDFs are stored under:
    misc/converted/<folder_name>/<file_stem>/<file_stem>.pdf
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .exceptions import ConversionError


class ConversionStrategy:
    """Interface for a document->PDF conversion backend."""

    def is_available(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def convert(self, src: Path, out_dir: Path, timeout: int) -> Path:  # pragma: no cover
        raise NotImplementedError


class LibreOfficeConverter(ConversionStrategy):
    """Convert documents to PDF using LibreOffice/soffice headless."""

    def __init__(self, binary: Optional[str] = None) -> None:
        # Prefer an explicit binary, else discover soffice/libreoffice on PATH.
        self._binary = binary or shutil.which("soffice") or shutil.which("libreoffice")

    def is_available(self) -> bool:
        return self._binary is not None

    def convert(self, src: Path, out_dir: Path, timeout: int = 180) -> Path:
        if not self.is_available():
            raise ConversionError(
                "LibreOffice/soffice not found on PATH. Install LibreOffice or "
                "configure another conversion strategy."
            )
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._binary,  # type: ignore[list-item]
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(src),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"LibreOffice conversion timed out after {timeout}s for {src}"
            ) from exc

        if proc.returncode != 0:
            raise ConversionError(
                f"LibreOffice failed (code {proc.returncode}) for {src}. "
                f"stdout={proc.stdout.strip()!r} stderr={proc.stderr.strip()!r}"
            )

        produced = out_dir / f"{src.stem}.pdf"
        if not produced.exists():
            # LibreOffice may name output differently in edge cases; fall back to
            # any single PDF produced in the output directory.
            pdfs = list(out_dir.glob("*.pdf"))
            if len(pdfs) == 1:
                produced = pdfs[0]
            else:
                raise ConversionError(
                    f"Conversion reported success but PDF not found for {src}. "
                    f"stdout={proc.stdout.strip()!r}"
                )
        return produced


class DocumentConverter:
    """Facade around a :class:`ConversionStrategy`.

    Encapsulates the target-path layout under ``misc/converted`` so callers do
    not need to know it.
    """

    def __init__(self, strategy: Optional[ConversionStrategy] = None) -> None:
        self.strategy: ConversionStrategy = strategy or LibreOfficeConverter()

    def is_available(self) -> bool:
        return self.strategy.is_available()

    def convert_to_pdf(
        self,
        src: Path,
        converted_root: Path,
        folder_name: str,
        logger: logging.Logger,
        timeout: int = 180,
    ) -> Path:
        """Convert ``src`` to PDF, returning the produced PDF path.

        Raises:
            ConversionError: on any failure (recorded per-file by the caller).
        """
        src = Path(src)
        out_dir = Path(converted_root) / folder_name / src.stem

        logger.info("Converting document to PDF: %s", src)
        if not self.is_available():
            raise ConversionError(
                f"No conversion backend available to convert {src.name}"
            )

        produced = self.strategy.convert(src, out_dir, timeout=timeout)
        logger.info("Converted %s -> %s", src.name, produced)
        return produced
