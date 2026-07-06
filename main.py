"""Entry point for the PaddleOCR document-processing pipeline.

Responsibilities (kept intentionally thin):
  * parse CLI arguments;
  * load configuration (defaults <- YAML <- CLI overrides);
  * resolve which subfolders of ``inputs/checking`` to process;
  * build a shared OcrRunner and process each folder;
  * no business logic lives here.

Examples:
    python main.py
    python main.py --target-folder "PI 292174"
    python main.py --config config/test_config.yaml
    python main.py --target-folder "PI 293267" --device cpu --overwrite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import apply_overrides, load_config
from src.document_converter import DocumentConverter
from src.document_rules import load_document_rules
from src.exceptions import ConfigError, OcrPipelineError, TargetFolderNotFound
from src.file_scanner import list_target_subfolders
from src.ocr_runner import OcrRunner
from src.logger_setup import get_folder_logger
from src.pipeline import process_folder

PROJECT_ROOT = Path(__file__).resolve().parent


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PaddleOCR document-processing pipeline (layout analysis).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a YAML config file (defaults used if omitted).",
    )
    parser.add_argument(
        "--target-folder",
        default=None,
        help='Process only this subfolder of inputs/checking (e.g. "PI 292174").',
    )
    parser.add_argument(
        "--process-all",
        action="store_true",
        help="Process all subfolders (overrides config).",
    )
    parser.add_argument(
        "--device",
        default=None,
        help='Inference device, e.g. "gpu:0" or "cpu".',
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing outputs instead of skipping.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip files whose output already exists (default behavior).",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level.",
    )
    return parser


def _cli_overrides(args: argparse.Namespace) -> dict:
    """Translate CLI args into AppConfig override values (None = leave as-is)."""
    overrides: dict = {}
    if args.target_folder is not None:
        overrides["target_folder"] = args.target_folder
        # An explicit target implies "not all" unless --process-all is given.
        overrides["process_all_folders"] = bool(args.process_all)
    if args.process_all:
        overrides["process_all_folders"] = True
        overrides["target_folder"] = None
    if args.device is not None:
        overrides["device"] = args.device
        overrides["use_gpu"] = not args.device.lower().startswith("cpu")
    if args.log_level is not None:
        overrides["log_level"] = args.log_level
    # skip/overwrite are mutually exclusive; overwrite wins if both passed.
    if args.overwrite:
        overrides["overwrite_existing"] = True
        overrides["skip_existing"] = False
    elif args.skip_existing:
        overrides["skip_existing"] = True
        overrides["overwrite_existing"] = False
    return overrides


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    # 1) Load + override + validate config.
    try:
        config = load_config(args.config, project_root=str(PROJECT_ROOT))
        config = apply_overrides(config, _cli_overrides(args))
        config.validate()
    except ConfigError as exc:
        print(f"[config error] {exc}", file=sys.stderr)
        return 2

    # 2) Resolve folders to process.
    try:
        folders = list_target_subfolders(
            config.input_root_path,
            config.target_folder,
            config.process_all_folders,
        )
    except TargetFolderNotFound as exc:
        print(f"[target error] {exc}", file=sys.stderr)
        return 3

    if not folders:
        print(
            "No folders to process. Set target_folder or process_all_folders=true.",
            file=sys.stderr,
        )
        return 0

    print(f"Folders to process ({len(folders)}):")
    for folder in folders:
        print(f"  - {folder.name}")

    # 3) Build the shared OCR runner once (pipeline is expensive to init).
    #    Use a top-level bootstrap logger for construction messages.
    boot_logger = get_folder_logger(
        "_run",
        config.logs_root_path,
        level=config.log_level,
        to_console=config.log_to_console,
    )
    ocr_runner = OcrRunner(config, boot_logger)
    converter = DocumentConverter()
    if not converter.is_available():
        boot_logger.warning(
            "LibreOffice not available: .doc/.docx/.odt conversion will fail per-file."
        )

    # Load document classification rules once (Option B segmentation).
    document_rules = None
    if config.enable_segmentation:
        try:
            document_rules = load_document_rules(config.document_rules_path_resolved)
            boot_logger.info(
                "Loaded document rules from %s (%d type(s))",
                config.document_rules_path_resolved,
                len(document_rules.types),
            )
        except ConfigError as exc:
            print(f"[config error] {exc}", file=sys.stderr)
            return 2

    # 4) Process each folder (failures isolated per folder/file).
    exit_code = 0
    for folder in folders:
        try:
            summary = process_folder(
                folder, config, ocr_runner, converter, document_rules
            )
            if summary.total_files_failed:
                exit_code = 1
        except OcrPipelineError as exc:
            print(f"[folder error] {folder.name}: {exc}", file=sys.stderr)
            exit_code = 1
        except Exception as exc:  # noqa: BLE001 - never crash the whole batch
            print(f"[unexpected error] {folder.name}: {exc}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
