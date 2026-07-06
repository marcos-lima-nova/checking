"""Centralized configuration for the OCR pipeline.

Configuration is expressed as dataclasses so that:
  * every configurable value has a documented default (the app can run without
    any YAML file);
  * a YAML file can override any subset of values;
  * command-line arguments can override the YAML on top of that.

Precedence (lowest to highest): dataclass defaults -> YAML file -> CLI args.

Nothing environment-sensitive is hard-coded in the business logic; it all flows
through :class:`AppConfig`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, List, Optional

import yaml

from .exceptions import ConfigError

# Default accepted extensions (lower-case, with leading dot).
DEFAULT_ACCEPTED_EXTENSIONS: List[str] = [
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".doc",
    ".docx",
    ".odt",
]

# Formats that must be converted to PDF before being sent to PaddleOCR.
DEFAULT_CONVERT_EXTENSIONS: List[str] = [".doc", ".docx", ".odt"]

# Image / PDF formats PaddleOCR can consume directly.
DEFAULT_DIRECT_EXTENSIONS: List[str] = [".png", ".jpg", ".jpeg", ".pdf"]


@dataclass
class PaddleOcrConfig:
    """PaddleOCR pipeline parameters.

    ``pipeline`` selects which pipeline class is instantiated in
    :mod:`src.ocr_runner`. ``PPStructureV3`` is the default and gives layout
    analysis. ``PaddleOCRVL`` is supported as a future/alternative option.

    The ``use_*`` flags mirror PaddleOCR 3.x construction / predict flags and are
    forwarded to the pipeline. ``None`` means "leave PaddleOCR default".
    """

    pipeline: str = "PPStructureV3"
    lang: Optional[str] = None
    ocr_version: Optional[str] = None

    use_doc_orientation_classify: Optional[bool] = True
    use_doc_unwarping: Optional[bool] = False
    use_textline_orientation: Optional[bool] = True
    use_table_recognition: Optional[bool] = True
    use_formula_recognition: Optional[bool] = True
    use_seal_recognition: Optional[bool] = True
    use_chart_recognition: Optional[bool] = False
    use_region_detection: Optional[bool] = None
    enable_hpi: bool = False
    cpu_threads: int = 8

    # Free-form extra kwargs passed straight to the pipeline constructor. Lets
    # advanced users tune model names/dirs/thresholds without code changes.
    extra: dict = field(default_factory=dict)


@dataclass
class PageImagesConfig:
    """Clean per-page image rendering (source of truth for box crops).

    These images are the ONLY acceptable crop source for the Tesseract fusion
    stage: never PaddleOCR's diagnostic/annotated outputs (``*_preprocessed_img.png``,
    ``*_overall_ocr_res.png``, ``*_layout_det_res.png``,
    ``*_layout_order_res.png``, ``*_region_det_res.png``,
    ``*_table_cell_img.png``).
    """

    enabled: bool = True
    output_folder_name: str = "page_images"
    render_dpi: int = 300
    image_format: str = "png"
    # If True, validate rendered size against the *_res.json width/height and
    # apply box scaling on mismatch (see page_image_validator.py).
    validate_against_paddle_json_size: bool = True
    allow_box_scaling: bool = True
    # A base image whose width is >= json_width * this ratio is treated as a
    # PaddleOCR diagnostic panel (e.g. the triple Original/Rotated/Unwarping
    # preview) and rejected as a crop source.
    suspicious_panel_ratio: float = 2.5


@dataclass
class TesseractConfig:
    """Per-box Tesseract OCR parameters (secondary OCR analysis)."""

    enabled: bool = True
    executable_path: str = "tesseract"
    language: str = "por+eng"
    psm: int = 7
    oem: int = 1
    timeout_seconds_per_box: int = 10
    save_box_crops: bool = True
    save_box_json: bool = True
    # Crop padding is 0 by default (exact crop). Only used if
    # allow_padding_for_debug is also True.
    crop_padding_px: int = 0
    allow_padding_for_debug: bool = False


@dataclass
class FusionConfig:
    """PaddleOCR x Tesseract per-box fusion parameters."""

    enabled: bool = True
    strategy: str = "select_highest_confidence_per_box"
    tie_margin: float = 0.03
    tie_breaker: str = "paddleocr"
    keep_alternatives: bool = True
    mark_conflicts: bool = True
    # Text-similarity threshold (0..1, via difflib) below which two selected
    # strings with close confidences are considered "too different" and the
    # box is flagged conflict_needs_review.
    conflict_text_similarity_max: float = 0.6


@dataclass
class AppConfig:
    """Top-level application configuration."""

    # --- Paths (relative to the project root unless absolute) ---
    input_root: str = "inputs/checking"
    output_root: str = "output"
    logs_root: str = "logs"
    misc_root: str = "misc"
    extracted_subdir: str = "extracted"
    converted_subdir: str = "converted"

    # --- Selective execution ---
    target_folder: Optional[str] = None
    process_all_folders: bool = True

    # --- Skip / overwrite behaviour ---
    skip_existing: bool = True
    overwrite_existing: bool = False

    # --- Post-OCR logical-document segmentation (Option B) ---
    enable_segmentation: bool = True
    document_rules_path: str = "config/document_rules.yaml"

    # --- Device ---
    use_gpu: bool = True
    device: Optional[str] = None  # e.g. "gpu:0" or "cpu"; None -> derived from use_gpu

    # --- Logging ---
    log_level: str = "INFO"
    log_to_console: bool = True

    # --- Extensions ---
    accepted_extensions: List[str] = field(
        default_factory=lambda: list(DEFAULT_ACCEPTED_EXTENSIONS)
    )
    document_convert_extensions: List[str] = field(
        default_factory=lambda: list(DEFAULT_CONVERT_EXTENSIONS)
    )

    # --- PaddleOCR ---
    paddleocr: PaddleOcrConfig = field(default_factory=PaddleOcrConfig)

    # --- PaddleOCR + Tesseract fusion stage ---
    page_images: PageImagesConfig = field(default_factory=PageImagesConfig)
    tesseract: TesseractConfig = field(default_factory=TesseractConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)

    # Absolute project root; resolved at load time. Not written to YAML.
    project_root: str = field(default_factory=lambda: str(Path.cwd()))

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #
    def resolved_device(self) -> str:
        """Return the device string to hand to PaddleOCR.

        Explicit ``device`` wins. Otherwise derive from ``use_gpu``.
        """
        if self.device:
            return self.device
        return "gpu:0" if self.use_gpu else "cpu"

    def _abs(self, value: str) -> Path:
        p = Path(value)
        if p.is_absolute():
            return p
        return Path(self.project_root) / p

    @property
    def input_root_path(self) -> Path:
        return self._abs(self.input_root)

    @property
    def output_root_path(self) -> Path:
        return self._abs(self.output_root)

    @property
    def logs_root_path(self) -> Path:
        return self._abs(self.logs_root)

    @property
    def misc_root_path(self) -> Path:
        return self._abs(self.misc_root)

    @property
    def extracted_root_path(self) -> Path:
        return self.misc_root_path / self.extracted_subdir

    @property
    def converted_root_path(self) -> Path:
        return self.misc_root_path / self.converted_subdir

    @property
    def document_rules_path_resolved(self) -> Path:
        return self._abs(self.document_rules_path)

    def normalized_accepted_extensions(self) -> List[str]:
        return [e.lower() for e in self.accepted_extensions]

    def normalized_convert_extensions(self) -> List[str]:
        return [e.lower() for e in self.document_convert_extensions]

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        """Validate coherence of configuration values.

        Raises :class:`ConfigError` on problems that would make the run invalid.
        Path *existence* of individual target folders is validated later by the
        scanner (so we can emit a precise error), but the input root itself must
        exist.
        """
        if not self.input_root_path.exists():
            raise ConfigError(
                f"input_root does not exist: {self.input_root_path}"
            )
        if not self.input_root_path.is_dir():
            raise ConfigError(
                f"input_root is not a directory: {self.input_root_path}"
            )

        for ext in self.accepted_extensions:
            if not ext.startswith("."):
                raise ConfigError(
                    f"accepted_extensions entries must start with '.': got {ext!r}"
                )

        if self.skip_existing and self.overwrite_existing:
            raise ConfigError(
                "skip_existing and overwrite_existing cannot both be True"
            )
        if not self.skip_existing and not self.overwrite_existing:
            raise ConfigError(
                "one of skip_existing / overwrite_existing must be True"
            )

        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            raise ConfigError(
                f"log_level must be one of {sorted(valid_levels)}, got {self.log_level!r}"
            )

        if not self.target_folder and not self.process_all_folders:
            raise ConfigError(
                "No work to do: target_folder is empty and process_all_folders is False"
            )

        if self.enable_segmentation and not self.document_rules_path_resolved.exists():
            raise ConfigError(
                f"document_rules_path does not exist: {self.document_rules_path_resolved}"
            )


# ---------------------------------------------------------------------- #
# Loading
# ---------------------------------------------------------------------- #
def _sub_dataclass_from_dict(data: dict, cls, section_name: str):
    """Build a nested dataclass (page_images/tesseract/fusion) from a dict.

    Unknown keys raise a clear :class:`ConfigError` (unlike ``paddleocr``, these
    sections have no free-form ``extra`` escape hatch).
    """
    data = dict(data or {})
    known = {f.name for f in fields(cls)}
    unknown = [k for k in data if k not in known]
    if unknown:
        raise ConfigError(f"Unknown {section_name!r} configuration keys: {unknown}")
    return cls(**data)


def _from_dict(data: dict, project_root: Optional[str]) -> AppConfig:
    """Build an :class:`AppConfig` from a plain dict (typically parsed YAML)."""
    data = dict(data or {})

    paddle_data = data.pop("paddleocr", None) or {}
    known_paddle = {f.name for f in fields(PaddleOcrConfig)}
    paddle_kwargs = {k: v for k, v in paddle_data.items() if k in known_paddle}
    # Unknown paddleocr keys are funneled into `extra` so they still reach the pipeline.
    unknown_paddle = {k: v for k, v in paddle_data.items() if k not in known_paddle}
    if unknown_paddle:
        paddle_kwargs.setdefault("extra", {}).update(unknown_paddle)
    paddle = PaddleOcrConfig(**paddle_kwargs)

    page_images_data = data.pop("page_images", None) or {}
    page_images = _sub_dataclass_from_dict(page_images_data, PageImagesConfig, "page_images")

    tesseract_data = data.pop("tesseract", None) or {}
    tesseract = _sub_dataclass_from_dict(tesseract_data, TesseractConfig, "tesseract")

    fusion_data = data.pop("fusion", None) or {}
    fusion = _sub_dataclass_from_dict(fusion_data, FusionConfig, "fusion")

    known = {f.name for f in fields(AppConfig)}
    kwargs = {
        k: v
        for k, v in data.items()
        if k in known and k not in ("paddleocr", "page_images", "tesseract", "fusion", "project_root")
    }
    unknown = [k for k in data if k not in known]
    if unknown:
        raise ConfigError(f"Unknown configuration keys: {unknown}")

    cfg = AppConfig(
        paddleocr=paddle,
        page_images=page_images,
        tesseract=tesseract,
        fusion=fusion,
        **kwargs,
    )
    if project_root:
        cfg.project_root = str(Path(project_root).resolve())
    return cfg


def load_config(
    config_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> AppConfig:
    """Load configuration from YAML (if provided) merged over defaults.

    Args:
        config_path: path to a YAML file. If ``None`` or the file does not
            exist, dataclass defaults are used.
        project_root: base directory used to resolve relative paths. Defaults to
            the current working directory.

    Returns:
        A validated-but-not-yet-validated :class:`AppConfig`. Call ``validate()``
        after applying any CLI overrides.
    """
    root = project_root or os.getcwd()

    if not config_path:
        cfg = AppConfig()
        cfg.project_root = str(Path(root).resolve())
        return cfg

    path = Path(config_path)
    if not path.is_absolute():
        path = Path(root) / path
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a mapping, got {type(data).__name__}")

    return _from_dict(data, root)


def apply_overrides(cfg: AppConfig, overrides: dict) -> AppConfig:
    """Apply CLI overrides (only non-None values) onto an existing config.

    Recognised keys map to top-level :class:`AppConfig` attributes.
    """
    for key, value in overrides.items():
        if value is None:
            continue
        if not hasattr(cfg, key):
            raise ConfigError(f"Unknown override: {key}")
        setattr(cfg, key, value)
    return cfg
