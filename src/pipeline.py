"""Per-folder orchestration.

Ties together scanning, zip extraction, document conversion, OCR, output
management and summary writing for a single analyzed folder. A shared
:class:`OcrRunner` is passed in so the (expensive) pipeline is built once and
reused across folders.

Failure isolation: any exception on a single file is logged with a full
traceback and recorded in the summary as ``failed``; processing continues with
the remaining files.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import AppConfig
from .document_converter import DocumentConverter
from .document_rules import DocumentRules
from .exceptions import ConversionError, ExtractionError
from .file_scanner import ScanResult, classify_extracted_files, scan_folder
from .logger_setup import get_folder_logger
from .ocr_runner import OcrRunner
from .output_manager import OutputManager
from . import preprocessing
from .segmentation import segment_file
from .summary_writer import (
    FILE_FAILED,
    FILE_PROCESSED,
    FILE_SKIPPED,
    SOURCE_CONVERTED,
    SOURCE_ORIGINAL,
    SOURCE_ZIP,
    FileRecord,
    FolderSummary,
)
from .zip_handler import extract_zip


@dataclass
class _WorkItem:
    """A single unit of OCR work after scanning/extraction."""

    path: Path                    # file to feed OCR (post-conversion)
    original_path: Path           # user-visible source path
    source_type: str
    zip_source: Optional[str] = None


def _config_snapshot(config: AppConfig) -> dict:
    """A compact, JSON-serializable view of the config used for this run."""
    return {
        "device": config.resolved_device(),
        "use_gpu": config.use_gpu,
        "skip_existing": config.skip_existing,
        "overwrite_existing": config.overwrite_existing,
        "accepted_extensions": config.normalized_accepted_extensions(),
        "document_convert_extensions": config.normalized_convert_extensions(),
        "pipeline": config.paddleocr.pipeline,
        "lang": config.paddleocr.lang,
        "ocr_version": config.paddleocr.ocr_version,
        "enable_segmentation": config.enable_segmentation,
        "document_rules_path": config.document_rules_path,
    }


def process_folder(
    subfolder: Path,
    config: AppConfig,
    ocr_runner: OcrRunner,
    converter: Optional[DocumentConverter] = None,
    document_rules: Optional[DocumentRules] = None,
) -> FolderSummary:
    """Process one subfolder of ``inputs/checking`` end to end."""
    subfolder = Path(subfolder)
    folder_name = subfolder.name
    converter = converter or DocumentConverter()

    logger = get_folder_logger(
        folder_name,
        config.logs_root_path,
        level=config.log_level,
        to_console=config.log_to_console,
    )
    log_file = str(getattr(logger, "log_file_path", ""))

    output_mgr = OutputManager(
        output_root=config.output_root_path,
        input_folder_name=folder_name,
        skip_existing=config.skip_existing,
        overwrite_existing=config.overwrite_existing,
    )
    output_mgr.folder_output_dir.mkdir(parents=True, exist_ok=True)

    summary = FolderSummary(
        input_folder=str(subfolder),
        output_folder=str(output_mgr.folder_output_dir),
        log_file=log_file,
        configuration=_config_snapshot(config),
    )

    folder_start = time.perf_counter()
    logger.info("=" * 70)
    logger.info("START folder: %s", subfolder)
    logger.info("Configuration: %s", summary.configuration)

    accepted = config.normalized_accepted_extensions()
    convert_exts = set(config.normalized_convert_extensions())

    # 1) Recursive scan of the original folder.
    scan: ScanResult = scan_folder(subfolder, accepted)
    logger.info(
        "Scan found: %d supported, %d zip, %d unsupported (total %d)",
        len(scan.supported_files),
        len(scan.zip_files),
        len(scan.unsupported_files),
        scan.total_found,
    )
    for path in scan.supported_files:
        logger.info("Supported file: %s", path)
    for path in scan.unsupported_files:
        logger.warning("Unsupported file (ignored): %s", path)

    # 2) Build the work queue from original supported files.
    work: List[_WorkItem] = [
        _WorkItem(path=p, original_path=p, source_type=SOURCE_ORIGINAL)
        for p in scan.supported_files
    ]

    # 3) Extract zips and enqueue their supported files.
    for zip_path in scan.zip_files:
        logger.info("Processing zip: %s", zip_path)
        try:
            extraction = extract_zip(
                zip_path,
                config.extracted_root_path,
                folder_name,
                logger,
            )
        except ExtractionError as exc:
            logger.error("Zip extraction failed for %s: %s", zip_path, exc)
            summary.add(
                FileRecord(
                    original_path=str(zip_path),
                    source_type=SOURCE_ZIP,
                    file_name=zip_path.name,
                    file_extension=".zip",
                    status=FILE_FAILED,
                    error=f"Extraction failed: {exc}",
                )
            )
            continue

        supported, nested_zips, unsupported = classify_extracted_files(
            extraction.all_files, accepted
        )
        for path in unsupported:
            logger.warning("Unsupported file in zip (ignored): %s", path)
        # Nested zips were already expanded by extract_zip; log if any remain.
        for path in nested_zips:
            logger.debug("Nested zip already expanded: %s", path)
        for path in supported:
            logger.info("Extracted supported file: %s (from %s)", path, zip_path.name)
            work.append(
                _WorkItem(
                    path=path,
                    original_path=path,
                    source_type=SOURCE_ZIP,
                    zip_source=str(zip_path),
                )
            )

    logger.info("Total work items to process: %d", len(work))

    # 4) Process each work item with per-file failure isolation.
    for item in work:
        _process_item(
            item,
            config,
            converter,
            output_mgr,
            subfolder,
            ocr_runner,
            summary,
            logger,
            convert_exts,
            document_rules,
        )

    # 5) Write the summary.
    summary.total_execution_seconds = time.perf_counter() - folder_start
    summary_path = output_mgr.summary_path()
    summary.write(summary_path)

    logger.info(
        "END folder: %s | processed=%d skipped=%d failed=%d | %.2fs",
        subfolder,
        summary.total_files_processed,
        summary.total_files_skipped,
        summary.total_files_failed,
        summary.total_execution_seconds,
    )
    logger.info("Summary written: %s", summary_path)
    logger.info("=" * 70)

    return summary


def _process_item(
    item: _WorkItem,
    config: AppConfig,
    converter: DocumentConverter,
    output_mgr: OutputManager,
    subfolder: Path,
    ocr_runner: OcrRunner,
    summary: FolderSummary,
    logger,
    convert_exts: set,
    document_rules: Optional[DocumentRules] = None,
) -> None:
    """Process a single work item, never raising to the caller."""
    started = datetime.now()
    start_perf = time.perf_counter()

    original_path = item.original_path
    original_ext = original_path.suffix.lower()

    record = FileRecord(
        original_path=str(original_path),
        source_type=item.source_type,
        zip_source=item.zip_source,
        original_extension=original_ext,
        file_name=original_path.name,
        file_extension=original_ext,
        started_at=started.isoformat(),
    )

    try:
        ocr_input = item.path
        needs_conversion = original_ext in convert_exts

        # 4a) Convert office documents to PDF if required.
        if needs_conversion:
            try:
                pdf_path = converter.convert_to_pdf(
                    original_path,
                    config.converted_root_path,
                    subfolder.name,
                    logger,
                )
                ocr_input = pdf_path
                record.source_type = (
                    SOURCE_CONVERTED if item.source_type == SOURCE_ORIGINAL else record.source_type
                )
                record.converted_file_path = str(pdf_path)
                record.conversion_status = "ok"
                record.file_extension = ".pdf"
            except ConversionError as exc:
                logger.error("Conversion failed for %s: %s", original_path, exc)
                record.conversion_status = "failed"
                record.status = FILE_FAILED
                record.error = f"Conversion failed: {exc}"
                _finalize(record, start_perf, logger, summary)
                return
        else:
            record.conversion_status = "not_needed"

        # 4b) Resolve output dir + skip/overwrite policy.
        output_dir = output_mgr.resolve_output_dir(original_path, subfolder)
        record.output_folder = str(output_dir)

        if not output_mgr.should_process(output_dir):
            logger.info("Skipping already-processed file: %s", original_path)
            record.status = FILE_SKIPPED
            _finalize(record, start_perf, logger, summary)
            return

        # 4c) Preprocessing hooks (v1: no-ops).
        ocr_input = preprocessing.preprocess_input(ocr_input, logger)

        # 4d) Run OCR. Native artifacts are saved into the raw_<stem> subfolder
        #     so they are never mixed with the pipeline-organized outputs.
        raw_dir = output_mgr.raw_output_dir(output_dir)
        record.raw_output_folder = str(raw_dir)
        ocr_runner.run(ocr_input, raw_dir)

        record.status = FILE_PROCESSED
        logger.info("Processed OK: %s -> %s", original_path, raw_dir)

        # 4e) Post-OCR logical-document segmentation (Option B). Failures here
        #     never flip OCR success to failed; they are logged and recorded.
        if config.enable_segmentation and document_rules is not None:
            seg = segment_file(
                source_file=original_path,
                output_dir=output_dir,
                raw_dir=raw_dir,
                rules=document_rules,
                logger=logger,
            )
            record.segmentation_status = seg.status
            record.segmentation_error = seg.error
            record.documents_detected = seg.summary_documents()
        elif not config.enable_segmentation:
            record.segmentation_status = "skipped"

    except Exception as exc:  # noqa: BLE001 - isolate per-file failures
        tb = traceback.format_exc()
        logger.error("FAILED processing %s: %s", original_path, exc)
        logger.error("Traceback:\n%s", tb)
        record.status = FILE_FAILED
        record.error = f"{type(exc).__name__}: {exc}\n{tb}"

    _finalize(record, start_perf, logger, summary)


def _finalize(record: FileRecord, start_perf: float, logger, summary: FolderSummary) -> None:
    """Stamp timing on a record, log duration, and add it to the summary."""
    finished = datetime.now()
    record.finished_at = finished.isoformat()
    record.duration_seconds = round(time.perf_counter() - start_perf, 3)
    logger.info(
        "File %s: status=%s duration=%.3fs",
        record.file_name,
        record.status,
        record.duration_seconds,
    )
    summary.add(record)
