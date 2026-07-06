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

Post-OCR logical-document segmentation (Option B):
    document_rules              - YAML-driven classification rules loader
    page_inventory              - inventory of raw artifacts, grouped by page
    page_text_indexer           - per-page text extraction + normalization
    page_classifier             - rule-based per-page classification
    document_segmenter          - continuation rule + grouping into documents
    document_artifact_organizer - copy per-page artifacts into documents/
    llm_ready_writer            - document_metadata.json + llm_ready.json
    manifest_writer             - source_manifest.json (ADK entry point)
    segmentation                - post-OCR segmentation orchestrator
    utils.text_normalizer       - text normalization for matching
    utils.path_utils            - sanitized folder/file naming

PaddleOCR + Tesseract per-box fusion stage:
    page_image_renderer         - clean per-page image rendering (crop source)
    page_image_validator        - validate/scale base image vs *_res.json size
    tesseract_box_extractor     - read PaddleOCR boxes from *_res.json
    box_cropper                 - crop exact boxes out of page_images/
    tesseract_runner            - thin wrapper around the tesseract binary
    tesseract_result_writer     - per-page overall_ocr_res_tesseract.json
    ocr_fusion                  - PaddleOCR x Tesseract per-box fusion
    fusion_summary_writer       - aggregate per-page fusion counters
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
    "document_rules",
    "page_inventory",
    "page_text_indexer",
    "page_classifier",
    "document_segmenter",
    "document_artifact_organizer",
    "llm_ready_writer",
    "manifest_writer",
    "segmentation",
    "utils",
    "page_image_renderer",
    "page_image_validator",
    "tesseract_box_extractor",
    "box_cropper",
    "tesseract_runner",
    "tesseract_result_writer",
    "ocr_fusion",
    "fusion_summary_writer",
]

__version__ = "0.1.0"
