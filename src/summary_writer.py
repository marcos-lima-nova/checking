"""Structured execution summary (``processing_summary.json``).

Produces one JSON file per analyzed folder, designed to be consumed later by an
ADK agent: it points to every native OCR output and records per-file status,
provenance (original / zip_extracted / converted), timings and full errors.

Schema mirrors the spec exactly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Folder-level status values.
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATUS_FAILED = "failed"

# Per-file status values.
FILE_PROCESSED = "processed"
FILE_SKIPPED = "skipped"
FILE_FAILED = "failed"

# Provenance values.
SOURCE_ORIGINAL = "original"
SOURCE_ZIP = "zip_extracted"
SOURCE_CONVERTED = "converted"


@dataclass
class FileRecord:
    """One entry in the ``files`` array of the summary."""

    original_path: str
    source_type: str = SOURCE_ORIGINAL
    zip_source: Optional[str] = None
    converted_file_path: Optional[str] = None
    original_extension: Optional[str] = None
    conversion_status: Optional[str] = None  # ok | failed | not_needed
    file_name: str = ""
    file_extension: str = ""
    output_folder: Optional[str] = None
    status: str = FILE_PROCESSED
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FolderSummary:
    """Aggregates records for one analyzed folder and serializes them."""

    input_folder: str
    output_folder: str
    log_file: str
    execution_datetime: str = field(default_factory=lambda: datetime.now().isoformat())
    configuration: Optional[dict] = None
    total_execution_seconds: float = 0.0
    records: List[FileRecord] = field(default_factory=list)

    def add(self, record: FileRecord) -> None:
        self.records.append(record)

    # --- Derived totals -------------------------------------------------- #
    @property
    def total_files_found(self) -> int:
        return len(self.records)

    @property
    def total_files_processed(self) -> int:
        return sum(1 for r in self.records if r.status == FILE_PROCESSED)

    @property
    def total_files_skipped(self) -> int:
        return sum(1 for r in self.records if r.status == FILE_SKIPPED)

    @property
    def total_files_failed(self) -> int:
        return sum(1 for r in self.records if r.status == FILE_FAILED)

    def folder_status(self) -> str:
        if self.total_files_failed and self.total_files_processed == 0 and self.total_files_skipped == 0:
            return STATUS_FAILED
        if self.total_files_failed:
            return STATUS_COMPLETED_WITH_ERRORS
        return STATUS_COMPLETED

    def to_dict(self) -> dict:
        return {
            "input_folder": self.input_folder,
            "output_folder": self.output_folder,
            "log_file": self.log_file,
            "execution_datetime": self.execution_datetime,
            "status": self.folder_status(),
            "total_files_found": self.total_files_found,
            "total_files_processed": self.total_files_processed,
            "total_files_skipped": self.total_files_skipped,
            "total_files_failed": self.total_files_failed,
            "total_execution_seconds": round(self.total_execution_seconds, 3),
            "configuration": self.configuration,
            "files": [r.to_dict() for r in self.records],
        }

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        return path
