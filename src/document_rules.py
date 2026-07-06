"""Loader and model for the document classification rules.

Rules live in ``config/document_rules.yaml`` and are NEVER hard-coded in Python
(acceptance criterion 6). This module parses that YAML into dataclasses, pre-
normalizes every term (so the classifier can match against normalized page text
directly), and validates thresholds/weights.

The loaded :class:`DocumentRules` is built once per run and passed down to the
classifier and segmenter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

from .exceptions import ConfigError
from .utils.text_normalizer import normalize_terms


@dataclass
class DocumentType:
    """A single document type with its scoring terms (pre-normalized)."""

    key: str
    label: str
    strong_terms: List[str] = field(default_factory=list)
    weak_terms: List[str] = field(default_factory=list)

    def max_points(self, strong_weight: float, weak_weight: float) -> float:
        """Maximum achievable score if every configured term matched."""
        return (
            len(self.strong_terms) * strong_weight
            + len(self.weak_terms) * weak_weight
        )


@dataclass
class Thresholds:
    classified: float = 0.75
    needs_review: float = 0.45
    unknown_below: float = 0.45
    continuation_min_confidence: float = 0.35


@dataclass
class DocumentRules:
    """All classification rules loaded from YAML."""

    strong_weight: float
    weak_weight: float
    thresholds: Thresholds
    types: Dict[str, DocumentType]

    def type_keys(self) -> List[str]:
        return list(self.types.keys())


def _as_float(value, name: str, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"document_rules: {name} must be a number, got {value!r}") from exc


def load_document_rules(path: str | Path) -> DocumentRules:
    """Load and validate rules from ``path``.

    Raises:
        ConfigError: if the file is missing, malformed, or has no document types.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"document_rules file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"document_rules must contain a mapping, got {type(data).__name__}"
        )

    weights = data.get("weights") or {}
    strong_weight = _as_float(weights.get("strong"), "weights.strong", 2.0)
    weak_weight = _as_float(weights.get("weak"), "weights.weak", 1.0)

    th = data.get("thresholds") or {}
    thresholds = Thresholds(
        classified=_as_float(th.get("classified"), "thresholds.classified", 0.75),
        needs_review=_as_float(th.get("needs_review"), "thresholds.needs_review", 0.45),
        unknown_below=_as_float(th.get("unknown_below"), "thresholds.unknown_below", 0.45),
        continuation_min_confidence=_as_float(
            th.get("continuation_min_confidence"),
            "thresholds.continuation_min_confidence",
            0.35,
        ),
    )

    raw_types = data.get("document_types") or {}
    if not isinstance(raw_types, dict) or not raw_types:
        raise ConfigError("document_rules: 'document_types' must be a non-empty mapping")

    types: Dict[str, DocumentType] = {}
    for key, spec in raw_types.items():
        spec = spec or {}
        label = str(spec.get("label", key))
        strong = normalize_terms(spec.get("strong_terms") or [])
        weak = normalize_terms(spec.get("weak_terms") or [])
        if not strong and not weak:
            raise ConfigError(
                f"document_rules: type {key!r} has no strong_terms/weak_terms"
            )
        types[str(key)] = DocumentType(
            key=str(key),
            label=label,
            strong_terms=strong,
            weak_terms=weak,
        )

    return DocumentRules(
        strong_weight=strong_weight,
        weak_weight=weak_weight,
        thresholds=thresholds,
        types=types,
    )
