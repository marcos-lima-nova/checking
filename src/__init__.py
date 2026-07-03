"""OCR pipeline package for PaddleOCR-based document processing.

Modules:
    config              - centralized configuration (dataclasses + YAML loader)
    logger_setup        - per-folder logging into logs/
    file_scanner        - discovery of subfolders and supported files
    zip_handler         - controlled extraction of .zip archives
    document_converter  - .doc/.docx/.odt -> .pdf conversion (LibreOffice)
    ocr_runner          - PaddleOCR pipeline wrapper (layout analysis)
    output_manager      - output paths and skip/overwrite policy
    summary_writer      - processing_summary.json generation (ADK-friendly)
    preprocessing       - extension hooks for future PDF/image preprocessing
    pipeline            - per-folder orchestrator
    exceptions          - project-specific exceptions
"""

__all__ = [
    "config",
    "logger_setup",
    "file_scanner",
    "zip_handler",
    "document_converter",
    "ocr_runner",
    "output_manager",
    "summary_writer",
    "preprocessing",
    "pipeline",
    "exceptions",
]

__version__ = "0.1.0"
