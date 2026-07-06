"""Text normalization for rule-based document classification.

The classifier matches YAML terms against page text. To make matching robust to
accents, casing, punctuation and whitespace variation, both the page text and
the configured terms are pushed through the same normalization pipeline.

Normalization rules (from plan_opcao_B.md, Etapa 3):
  * uppercase;
  * strip accents/diacritics;
  * collapse multiple whitespace into a single space;
  * remove punctuation that is irrelevant for matching;
  * the original text is always preserved in parallel by the caller.

The functions here are pure (no I/O) so they are trivially testable.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

# Characters kept during normalization: letters, digits and single spaces.
# Everything else (punctuation, symbols) is turned into a space so that, e.g.,
# "P.I." and "P I" both normalize to "P I".
_NON_MATCHING = re.compile(r"[^0-9A-Za-z\u00C0-\u017F ]+")
_MULTISPACE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Remove diacritics using Unicode NFKD decomposition."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Normalize ``text`` for matching.

    Returns an uppercase, accent-free, single-spaced string with matching-
    irrelevant punctuation removed. Never raises on non-string input; callers
    should pass strings but ``None`` is coerced to an empty string defensively.
    """
    if not text:
        return ""
    # Accents first (before we drop non-ASCII punctuation), then uppercase.
    text = strip_accents(str(text))
    text = text.upper()
    # Replace punctuation/symbols with spaces, then collapse whitespace.
    text = _NON_MATCHING.sub(" ", text)
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def normalize_lines(lines: Iterable[str]) -> str:
    """Normalize a sequence of text lines into one normalized blob.

    Lines are joined with a single space after individual normalization, which
    keeps multi-word terms matchable even when they span OCR line breaks within
    the joined text.
    """
    normalized = [normalize(line) for line in lines]
    normalized = [n for n in normalized if n]
    return _MULTISPACE.sub(" ", " ".join(normalized)).strip()


def normalize_terms(terms: Iterable[str]) -> List[str]:
    """Normalize a list of configured terms, dropping empties/duplicates.

    Order is preserved (first occurrence wins) so scoring/logging stays stable.
    """
    seen = set()
    out: List[str] = []
    for term in terms or []:
        norm = normalize(term)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out
