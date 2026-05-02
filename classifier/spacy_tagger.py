"""spaCy-powered local email classifier."""

from __future__ import annotations

from dataclasses import dataclass

import spacy
from spacy.matcher import PhraseMatcher
from spacy.tokens import Doc

MODEL_NAME = "en_core_web_md"
LOW_CONFIDENCE_THRESHOLD = 0.6
NER_BOOST = 0.18
KEYWORD_WEIGHT = 0.18
SIMILARITY_WEIGHT = 0.32
MAX_CONFIDENCE = 0.98

CATEGORIES = (
    "Work",
    "Finance",
    "Newsletters",
    "Social",
    "Promotions",
    "Travel",
    "Health",
    "Shopping",
    "Family",
    "Other",
)

KEYWORDS: dict[str, list[str]] = {
    "Work": [
        "meeting",
        "deadline",
        "project",
        "client",
        "agenda",
        "report",
        "contract",
        "proposal",
        "interview",
        "office",
    ],
    "Finance": [
        "invoice",
        "payment",
        "bank",
        "transaction",
        "receipt",
        "salary",
        "statement",
        "tax",
        "refund",
        "balance",
    ],
    "Newsletters": [
        "newsletter",
        "digest",
        "roundup",
        "subscribe",
        "unsubscribe",
        "weekly",
        "edition",
        "update",
        "latest",
        "read more",
    ],
    "Social": [
        "friend request",
        "liked",
        "commented",
        "shared",
        "mentioned",
        "followers",
        "connection",
        "notification",
        "message request",
        "tagged",
    ],
    "Promotions": [
        "sale",
        "discount",
        "coupon",
        "offer",
        "promo",
        "deal",
        "limited time",
        "clearance",
        "save",
        "free shipping",
    ],
    "Travel": [
        "flight",
        "booking",
        "hotel",
        "itinerary",
        "reservation",
        "trip",
        "boarding",
        "airline",
        "check-in",
        "gate",
    ],
    "Health": [
        "doctor",
        "appointment",
        "clinic",
        "pharmacy",
        "prescription",
        "medical",
        "wellness",
        "insurance",
        "lab",
        "patient",
    ],
    "Shopping": [
        "order",
        "delivery",
        "shipment",
        "tracking",
        "package",
        "cart",
        "purchase",
        "return",
        "seller",
        "delivered",
    ],
    "Family": [
        "family",
        "mom",
        "dad",
        "parents",
        "sister",
        "brother",
        "birthday",
        "dinner",
        "kids",
        "home",
    ],
    "Other": [],
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Work": "work business office meeting project task client job professional",
    "Finance": "finance money bank payment invoice receipt salary tax transaction",
    "Newsletters": "newsletter digest subscription publication update roundup articles",
    "Social": "social media notification friend comment like share message connection",
    "Promotions": "promotion marketing sale discount coupon offer deal savings",
    "Travel": "travel flight hotel booking reservation trip itinerary airline",
    "Health": "health doctor clinic medical appointment pharmacy prescription wellness",
    "Shopping": "shopping online order package delivery shipment tracking purchase",
    "Family": "family relatives parents home birthday dinner personal plans",
    "Other": "general email miscellaneous message uncategorized",
}

NER_HINTS: dict[str, set[str]] = {
    "Finance": {"MONEY"},
    "Travel": {"GPE", "LOC", "FAC"},
    "Work": {"ORG"},
    "Family": {"PERSON"},
    "Health": {"DATE"},
}


@dataclass(frozen=True)
class Classification:
    """One email classification result."""

    label: str
    confidence: float


class SpacyEmailTagger:
    """Classify emails with spaCy keyword matching, NER, and vectors."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        """Load spaCy model and prepare matchers.

        Args:
            model_name: spaCy model package name.
        """
        try:
            self.nlp = spacy.load(model_name)
        except OSError as exc:
            raise OSError(
                f"spaCy model '{model_name}' is not installed. "
                f"Run: python -m spacy download {model_name}"
            ) from exc

        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        for label, keywords in KEYWORDS.items():
            if keywords:
                self.matcher.add(label, [self.nlp.make_doc(keyword) for keyword in keywords])

        self.category_docs = {
            label: self.nlp(description)
            for label, description in CATEGORY_DESCRIPTIONS.items()
        }

    def classify_email(self, subject: str, body: str) -> dict[str, float | str]:
        """Classify one email.

        Args:
            subject: Email subject text.
            body: Email body or snippet text.

        Returns:
            Dictionary with label and confidence. Labels below 0.6 confidence are
            normalized to Other.
        """
        doc = self.nlp(_combine_text(subject, body))
        result = self._score(doc)
        if result.confidence < LOW_CONFIDENCE_THRESHOLD:
            return {"label": "Other", "confidence": round(result.confidence, 3)}
        return {"label": result.label, "confidence": round(result.confidence, 3)}

    def _score(self, doc: Doc) -> Classification:
        """Score every category and return the best result."""
        keyword_counts = self._keyword_counts(doc)
        best = Classification(label="Other", confidence=0.35)

        for label in CATEGORIES:
            if label == "Other":
                continue
            keyword_score = min(keyword_counts.get(label, 0) * KEYWORD_WEIGHT, 0.54)
            ner_score = NER_BOOST if self._has_ner_hint(doc, label) else 0.0
            similarity_score = self._similarity(doc, label) * SIMILARITY_WEIGHT
            confidence = min(keyword_score + ner_score + similarity_score, MAX_CONFIDENCE)
            if confidence > best.confidence:
                best = Classification(label=label, confidence=confidence)

        return best

    def _keyword_counts(self, doc: Doc) -> dict[str, int]:
        """Count keyword matches by category."""
        counts = {label: 0 for label in CATEGORIES}
        for match_id, _start, _end in self.matcher(doc):
            counts[self.nlp.vocab.strings[match_id]] += 1
        return counts

    def _has_ner_hint(self, doc: Doc, label: str) -> bool:
        """Return whether named entities support a category."""
        hints = NER_HINTS.get(label, set())
        return any(entity.label_ in hints for entity in doc.ents)

    def _similarity(self, doc: Doc, label: str) -> float:
        """Return bounded vector similarity for a category."""
        category_doc = self.category_docs[label]
        if not doc.has_vector or not category_doc.has_vector:
            return 0.0
        return max(0.0, min(1.0, doc.similarity(category_doc)))


_TAGGER: SpacyEmailTagger | None = None


def get_tagger() -> SpacyEmailTagger:
    """Return a cached spaCy email tagger instance."""
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = SpacyEmailTagger()
    return _TAGGER


def classify_email(subject: str, body: str) -> dict[str, float | str]:
    """Classify one email with the cached spaCy model.

    Args:
        subject: Email subject text.
        body: Email body or snippet text.

    Returns:
        Dictionary with label and confidence.
    """
    return get_tagger().classify_email(subject, body)


def _combine_text(subject: str, body: str) -> str:
    """Combine subject and body, giving subject slight extra weight."""
    clean_subject = " ".join((subject or "").split())
    clean_body = " ".join((body or "").split())
    return f"{clean_subject}\n{clean_subject}\n{clean_body[:3000]}".strip()
