
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_anonymizer import AnonymizerEngine

logger = logging.getLogger("opsmind.presidio")

@dataclass
class PresidioScanResult:
    original_text: str
    anonymized_text: str
    has_pii: bool
    detected_entities: list[str] = field(default_factory=list)
    entity_counts: dict[str, int] = field(default_factory=dict)

class PresidioPIIEngine:
    """Singleton Microsoft Presidio analyzer and anonymizer pipeline."""

    _analyzer: AnalyzerEngine | None = None
    _anonymizer: AnonymizerEngine | None = None

    @classmethod
    def get_analyzer(cls) -> AnalyzerEngine:
        if cls._analyzer is None:
            logger.info("Initializing Microsoft Presidio AnalyzerEngine with local spaCy model...")
            nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
            cls._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        return cls._analyzer

    @classmethod
    def get_anonymizer(cls) -> AnonymizerEngine:
        if cls._anonymizer is None:
            cls._anonymizer = AnonymizerEngine()
        return cls._anonymizer

    @classmethod
    def sanitize(
        cls,
        text: str,
        entities: list[str] | None = None,
        language: str = "en",
    ) -> PresidioScanResult:
        """Scans input text for sensitive PII and returns anonymized sanitized text."""
        if not text or not text.strip():
            return PresidioScanResult(
                original_text=text,
                anonymized_text=text,
                has_pii=False,
            )

        target_entities = entities or [
            "PERSON",
            "PHONE_NUMBER",
            "EMAIL_ADDRESS",
            "CREDIT_CARD",
            "IBAN_CODE",
            "CRYPTO",
            "US_SSN",
            "IP_ADDRESS",
        ]

        try:
            analyzer = cls.get_analyzer()
            anonymizer = cls.get_anonymizer()

            results = analyzer.analyze(
                text=text,
                entities=target_entities,
                language=language,
            )

            if not results:
                return PresidioScanResult(
                    original_text=text,
                    anonymized_text=text,
                    has_pii=False,
                )

            anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
            detected_types = [r.entity_type for r in results]
            counts: dict[str, int] = {}
            for t in detected_types:
                counts[t] = counts.get(t, 0) + 1

            logger.info("Presidio PII detected and anonymized: %s", counts)

            return PresidioScanResult(
                original_text=text,
                anonymized_text=anonymized.text,
                has_pii=True,
                detected_entities=sorted(list(set(detected_types))),
                entity_counts=counts,
            )
        except Exception as exc:
            logger.warning("Presidio PII scanning encountered error, falling back: %s", exc)
            return PresidioScanResult(
                original_text=text,
                anonymized_text=text,
                has_pii=False,
            )
