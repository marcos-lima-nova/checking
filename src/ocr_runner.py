"""PaddleOCR pipeline wrapper (layout analysis).

All direct calls to PaddleOCR live here so the rest of the codebase never
imports paddleocr. This isolation makes it easy to:
  * swap the pipeline (PPStructureV3 <-> PaddleOCRVL) via config;
  * upgrade to a different PaddleOCR version and adapt in one place;
  * add preprocessing/fallback logic around the OCR call later.

Verified against PaddleOCR 3.7.0:
  * ``PPStructureV3`` gives layout analysis and yields the native artifacts
    (``*_res.json``, ``*.md``, ``*_layout_det_res.png``, ``imgs/`` ...).
  * The pipeline is constructed once (expensive) and reused for every file.
  * ``.predict(input)`` returns an iterable of per-page results.
  * ``result.save_all(save_path=<dir>)`` writes ALL native artifacts.
  * ``device`` is accepted as a constructor kwarg ("gpu:0" / "cpu").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from .config import AppConfig
from .exceptions import ConfigError, OcrExecutionError

# Pipeline names we know how to build. Kept as strings so config stays declarative.
_SUPPORTED_PIPELINES = {"PPStructureV3", "PaddleOCRVL"}


class OcrRunner:
    """Thin, config-driven wrapper around a PaddleOCR pipeline.

    Construct once per execution; call :meth:`run` per input file.
    """

    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._pipeline = None  # lazily built on first use

    # ------------------------------------------------------------------ #
    # Pipeline construction
    # ------------------------------------------------------------------ #
    def _build_kwargs(self) -> Dict[str, Any]:
        """Assemble constructor kwargs from config, dropping None values."""
        pc = self.config.paddleocr
        kwargs: Dict[str, Any] = {
            "device": self.config.resolved_device(),
            "use_doc_orientation_classify": pc.use_doc_orientation_classify,
            "use_doc_unwarping": pc.use_doc_unwarping,
            "use_textline_orientation": pc.use_textline_orientation,
            "use_table_recognition": pc.use_table_recognition,
            "use_formula_recognition": pc.use_formula_recognition,
            "use_seal_recognition": pc.use_seal_recognition,
            "use_chart_recognition": pc.use_chart_recognition,
            "use_region_detection": pc.use_region_detection,
            "lang": pc.lang,
            "ocr_version": pc.ocr_version,
            "enable_hpi": pc.enable_hpi,
        }
        # Drop keys with None so we defer to PaddleOCR defaults.
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        # Merge any free-form extras (may override the above intentionally).
        kwargs.update(pc.extra or {})
        return kwargs

    def _build_pipeline(self):
        pipeline_name = self.config.paddleocr.pipeline
        if pipeline_name not in _SUPPORTED_PIPELINES:
            raise ConfigError(
                f"Unsupported pipeline {pipeline_name!r}. "
                f"Supported: {sorted(_SUPPORTED_PIPELINES)}"
            )

        kwargs = self._build_kwargs()
        self.logger.info(
            "Initializing PaddleOCR pipeline %s (device=%s)",
            pipeline_name,
            self.config.resolved_device(),
        )
        self.logger.debug("Pipeline kwargs: %s", kwargs)

        # Import here so importing this module never forces loading paddleocr.
        import paddleocr

        try:
            pipeline_cls = getattr(paddleocr, pipeline_name)
        except AttributeError as exc:
            raise ConfigError(
                f"Pipeline {pipeline_name!r} is not available in the installed "
                f"paddleocr version."
            ) from exc

        # Some flags are constructor args in one pipeline and predict args in
        # another. We try the full kwargs first, then progressively drop unknown
        # ones so we stay robust across pipeline/version differences.
        return self._instantiate_robustly(pipeline_cls, kwargs, pipeline_name)

    def _instantiate_robustly(self, pipeline_cls, kwargs: Dict[str, Any], name: str):
        """Instantiate the pipeline, tolerating kwargs it does not accept.

        We do not hard-code an assumed signature (per the spec). If a kwarg is
        rejected as unexpected, we remove it and retry.
        """
        current = dict(kwargs)
        while True:
            try:
                return pipeline_cls(**current)
            except TypeError as exc:
                dropped = self._drop_offending_kwarg(current, exc)
                if dropped is None:
                    raise
                self.logger.warning(
                    "Pipeline %s rejected kwarg %r; retrying without it.",
                    name,
                    dropped,
                )

    @staticmethod
    def _drop_offending_kwarg(kwargs: Dict[str, Any], exc: TypeError):
        """If ``exc`` names an unexpected kwarg present in ``kwargs``, drop it."""
        msg = str(exc)
        for key in list(kwargs.keys()):
            if key in msg and "unexpected keyword" in msg:
                kwargs.pop(key, None)
                return key
        return None

    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = self._build_pipeline()
        return self._pipeline

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def run(self, input_path: Path, output_dir: Path) -> List[Path]:
        """Run OCR on ``input_path`` and save native artifacts into ``output_dir``.

        Handles multi-page PDFs natively: ``predict`` yields one result per page
        and every result is saved into the same ``output_dir`` (consolidated per
        file), following PaddleOCR's native behavior.

        Returns:
            The list of top-level artifact paths present in ``output_dir`` after
            saving (best-effort, for logging/summary).

        Raises:
            OcrExecutionError: if prediction/saving fails.
        """
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info("Running OCR on %s", input_path)
        try:
            results = self.pipeline.predict(str(input_path))
            page_count = 0
            for result in results:
                result.save_all(save_path=str(output_dir))
                page_count += 1
            self.logger.info(
                "OCR produced %d page result(s) for %s", page_count, input_path.name
            )
        except Exception as exc:  # noqa: BLE001 - re-wrapped for the caller
            raise OcrExecutionError(
                f"PaddleOCR failed on {input_path}: {exc}"
            ) from exc

        artifacts = sorted(p for p in output_dir.iterdir())
        return artifacts
