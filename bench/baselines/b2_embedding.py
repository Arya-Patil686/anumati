"""B2 — B0 plus semantic similarity between the instruction and the product text.

The "just use embeddings" shortcut. It scores how alike two strings are and thresholds
it, which is a different question from whether a constraint is met: a trail shoe is very
similar to a running shoe, and a 4.9 kg bag is very similar to a 5 kg one.

The encoder is reported in the results, because the number means nothing without it.
`sentence-transformers` is used when the configured model is available locally;
otherwise this falls back to character n-gram TF-IDF cosine, which is a weaker encoder
but needs no download and handles Hinglish transliteration tolerably.
"""

from __future__ import annotations

from app.models.cart import Cart
from app.models.common import Decision
from app.models.mandate import IntentMandate

from bench.baselines import timed
from bench.baselines.b0_numeric import B0Numeric

SIMILARITY_THRESHOLD = 0.45


class _TfidfEncoder:
    """Character n-gram cosine. Fitted on the corpus it is asked about."""

    name = "tfidf-char-3-5"

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        self._fitted = False

    def fit(self, corpus: list[str]) -> None:
        if corpus:
            self._vectorizer.fit(corpus)
            self._fitted = True

    def similarity(self, left: str, right: str) -> float:
        from sklearn.metrics.pairwise import cosine_similarity

        if not self._fitted:
            self.fit([left, right])
        matrix = self._vectorizer.transform([left, right])
        return float(cosine_similarity(matrix[0], matrix[1])[0][0])


class _SentenceTransformerEncoder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.name = model_name

    def fit(self, corpus: list[str]) -> None:
        return None

    def similarity(self, left: str, right: str) -> float:
        from sentence_transformers.util import cos_sim

        vectors = self._model.encode([left, right], convert_to_tensor=True)
        return float(cos_sim(vectors[0], vectors[1])[0][0])


def build_encoder(model_name: str, *, prefer_transformers: bool = False):
    """Local model when asked for and available, TF-IDF otherwise. Never downloads."""
    if prefer_transformers:
        try:
            return _SentenceTransformerEncoder(model_name)
        except Exception as exc:  # noqa: BLE001 — any failure falls back to TF-IDF
            print(f"  sentence-transformers unavailable ({exc}); using TF-IDF")
    return _TfidfEncoder()


class B2Embedding:
    name = "B2 embedding"
    description = "B0 plus instruction/product similarity above a threshold"

    def __init__(self, encoder=None, threshold: float = SIMILARITY_THRESHOLD) -> None:
        self._numeric = B0Numeric()
        self.encoder = encoder or build_encoder("intfloat/multilingual-e5-small")
        self.threshold = threshold

    @property
    def encoder_name(self) -> str:
        return getattr(self.encoder, "name", "unknown")

    @timed
    def predict(  # type: ignore[override]
        self, mandate: IntentMandate, cart: Cart
    ) -> tuple[Decision, float, bool, str]:
        numeric = self._numeric.predict(mandate, cart)
        if numeric.decision is Decision.VIOLATES:
            return Decision.VIOLATES, numeric.confidence, False, numeric.note

        product_text = " ".join(
            f"{i.title} {i.description} {i.brand} {i.category_path}" for i in cart.items
        )
        score = self.encoder.similarity(mandate.natural_language, product_text)

        if score >= self.threshold:
            return Decision.CONFORMS, min(0.99, score), False, f"similarity {score:.2f}"
        return Decision.VIOLATES, min(0.99, 1 - score), False, f"similarity {score:.2f}"
