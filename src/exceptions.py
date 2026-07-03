"""Project-specific exceptions for the OCR pipeline.

Keeping domain errors in one place makes it easy to distinguish expected,
recoverable failures (e.g. a single file failing conversion) from unexpected
programming errors.
"""


class OcrPipelineError(Exception):
    """Base class for all pipeline-related errors."""


class TargetFolderNotFound(OcrPipelineError):
    """Raised when a requested ``target_folder`` does not exist under the input root.

    This is a fatal, execution-level error: if the user explicitly asked for a
    folder that does not exist, we stop rather than silently doing nothing.
    """


class ConfigError(OcrPipelineError):
    """Raised when configuration is missing required values or is inconsistent."""


class ConversionError(OcrPipelineError):
    """Raised when a document (.doc/.docx/.odt) cannot be converted to PDF.

    This is a per-file, recoverable error: it must be logged in detail and
    recorded in the summary, but must NOT stop the whole execution.
    """


class ExtractionError(OcrPipelineError):
    """Raised when a .zip archive cannot be extracted.

    Recoverable at the archive level: logged and recorded, execution continues
    with the remaining files.
    """


class UnsupportedFormatError(OcrPipelineError):
    """Raised when a file extension is not in the accepted list."""


class OcrExecutionError(OcrPipelineError):
    """Raised when the PaddleOCR pipeline fails on a specific input.

    Recoverable at the file level.
    """
