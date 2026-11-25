"""Category taxonomy: the controlled vocabulary and the words people actually type.

Used by the Mandate Builder to turn "daudne wale shoes" into
`Footwear > Sports > Running`, and by L2 to decide whether a cart item's category is the
one the user asked for.

Terms listed under `near_misses` resolve to a primary path but are marked
`is_near_miss`, which is how "trainers" or "sports shoes" end up producing a genuinely
AMBIGUOUS verdict instead of a confident wrong one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import REPO_ROOT

TAXONOMY_PATH = REPO_ROOT / "data" / "taxonomy.yaml"

_PUNCT = re.compile(r"[^\w\s>&-]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise_term(term: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace. Matching is done on this."""
    return _SPACE.sub(" ", _PUNCT.sub(" ", term.lower())).strip()


@dataclass(frozen=True)
class TaxonomyMatch:
    """What a term resolved to, and how sure we are of it."""

    term: str
    path: str
    is_near_miss: bool = False
    alternatives: tuple[str, ...] = ()

    @property
    def confidence(self) -> float:
        """Near-miss terms are deliberately not confident; L2/L3 settle them."""
        return 0.60 if self.is_near_miss else 0.99


@dataclass
class Taxonomy:
    paths: list[str] = field(default_factory=list)
    synonyms: dict[str, str] = field(default_factory=dict)
    near_misses: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Taxonomy:
        raw = yaml.safe_load((path or TAXONOMY_PATH).read_text(encoding="utf-8")) or {}
        paths = list(raw.get("paths") or [])
        synonyms = {normalise_term(k): v for k, v in (raw.get("synonyms") or {}).items()}
        near_misses = {normalise_term(k): v for k, v in (raw.get("near_misses") or {}).items()}

        # A path is always a synonym for itself, as is its leaf.
        for p in paths:
            synonyms.setdefault(normalise_term(p), p)
            synonyms.setdefault(normalise_term(leaf_of(p)), p)

        return cls(paths=paths, synonyms=synonyms, near_misses=near_misses)

    # --- lookup --------------------------------------------------------------

    def resolve(self, term: str) -> TaxonomyMatch | None:
        """Exact match on the whole term. Returns None when nothing matches."""
        key = normalise_term(term)

        if key in self.near_misses:
            entry = self.near_misses[key]
            return TaxonomyMatch(
                term=key,
                path=entry["primary"],
                is_near_miss=True,
                alternatives=tuple(entry.get("also") or ()),
            )
        if key in self.synonyms:
            return TaxonomyMatch(term=key, path=self.synonyms[key])
        return None

    def find_in(self, text: str) -> TaxonomyMatch | None:
        """Longest known term appearing anywhere in a free-text instruction.

        Longest-first matters: "running shoes" must beat "shoes", or every instruction
        about running shoes would resolve to the near-miss bucket.
        """
        haystack = normalise_term(text)
        best: TaxonomyMatch | None = None
        best_len = 0

        for key in (*self.synonyms, *self.near_misses):
            if len(key) <= best_len:
                continue
            if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", haystack):
                match = self.resolve(key)
                if match is not None:
                    best, best_len = match, len(key)
        return best

    def is_known_path(self, path: str) -> bool:
        return path in self.paths

    def leaves(self) -> list[str]:
        return [leaf_of(p) for p in self.paths]

    def under(self, prefix: str) -> list[str]:
        """Every path at or below a prefix, e.g. 'Footwear > Sports'."""
        return [p for p in self.paths if p == prefix or p.startswith(f"{prefix} > ")]


def leaf_of(path: str) -> str:
    return path.split(">")[-1].strip()


def top_of(path: str) -> str:
    return path.split(">")[0].strip()


@lru_cache
def get_taxonomy() -> Taxonomy:
    return Taxonomy.load()
