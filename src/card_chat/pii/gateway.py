"""Centralized PII Protection Gateway using Microsoft Presidio and reversible Token Vault."""
from dataclasses import dataclass
import logging
import re
from typing import Any

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_anonymizer import AnonymizerEngine

from card_chat.pii.recognizers import (
    AustralianBSBRecognizer,
    AustralianAccountNumberRecognizer,
    AustralianTFNRecognizer,
    AustralianMedicareRecognizer,
    AustralianPhoneNumberRecognizer,
    BankingCredentialsRecognizer,
    CreditCardNumberRecognizer,
)
from card_chat.pii.vault import TokenVault, InMemoryTokenVault
from card_chat.model_context import PRIVATE_PASTE, PRIVATE_NOTICE, REFERENCE

logger = logging.getLogger(__name__)


class PIILeakageError(RuntimeError):
    """Raised when an outbound payload fails the privacy-preserving fail-closed check."""
    pass


@dataclass
class SanitizationResult:
    text: str
    tokens_created: dict[str, str]  # token -> raw_value
    secrets_detected: bool
    pii_entities_found: list[str]


class PIIGateway:
    """Centralized PII Gateway providing inbound/outbound sanitization, tokenization,

    and fail-closed outbound checks before any external model or telemetry call.
    """

    REVERSIBLE_ENTITIES = {
        "AU_ACCOUNT_NUMBER",
        "AU_BSB",
        "CREDIT_CARD",
        "IBAN_CODE",
    }

    IRREVERSIBLE_ENTITIES = {
        "AU_TFN",
        "AU_MEDICARE",
        "AU_PHONE_NUMBER",
        "PHONE_NUMBER",
        "EMAIL_ADDRESS",
        "PERSON",
        "US_SSN",
    }

    SECRET_ENTITIES = {
        "BANKING_SECRET",
    }

    def __init__(self, vault: TokenVault | None = None, score_threshold: float = 0.4):
        self.vault = vault or InMemoryTokenVault()
        self.score_threshold = score_threshold

        # Initialize Presidio Analyzer with custom Australian and Banking recognizers
        import spacy
        from presidio_analyzer.nlp_engine import SpacyNlpEngine

        nlp_engine = SpacyNlpEngine()
        try:
            if spacy.util.is_package("en_core_web_sm"):
                nlp_engine.load()
            else:
                nlp_engine.nlp = {"en": spacy.blank("en")}
        except Exception:
            nlp_engine.nlp = {"en": spacy.blank("en")}

        registry = RecognizerRegistry()
        registry.load_predefined_recognizers(nlp_engine=nlp_engine)
        registry.add_recognizer(AustralianBSBRecognizer())
        registry.add_recognizer(AustralianAccountNumberRecognizer())
        registry.add_recognizer(AustralianTFNRecognizer())
        registry.add_recognizer(AustralianMedicareRecognizer())
        registry.add_recognizer(AustralianPhoneNumberRecognizer())
        registry.add_recognizer(BankingCredentialsRecognizer())
        registry.add_recognizer(CreditCardNumberRecognizer())

        self.analyzer = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine)
        self.anonymizer = AnonymizerEngine()

    def sanitize_input(
        self,
        text: str,
        owner: str,
        session_id: str,
        workflow_id: str = "default",
        private_values: tuple[str, ...] = (),
    ) -> SanitizationResult:
        """Sanitize customer input before LLM calls:

        1. Redact banking credentials, PINs, CVVs, API keys (never sent to LLM).
        2. Tokenize permitted banking identifiers (account numbers, cards) into reversible tokens.
        3. Anonymize irreversible PII (TFN, Medicare, Email, Phone).
        """
        if not isinstance(text, str) or not text.strip():
            return SanitizationResult(text=text, tokens_created={}, secrets_detected=False, pii_entities_found=[])

        tokens_created: dict[str, str] = {}
        entities_found: list[str] = []

        # Analyze text for PII
        results = self.analyzer.analyze(
            text=text,
            language="en",
            score_threshold=self.score_threshold,
        )

        # Sort results descending by start offset to perform in-place replacement
        sorted_results = sorted(results, key=lambda r: (r.start, -r.end), reverse=True)

        secrets_detected = any(r.entity_type in self.SECRET_ENTITIES for r in sorted_results)
        if secrets_detected:
            # Sensitive authentication secrets or credentials detected
            return SanitizationResult(
                text=PRIVATE_PASTE,
                tokens_created={},
                secrets_detected=True,
                pii_entities_found=[r.entity_type for r in sorted_results],
            )

        sanitized_chars = list(text)

        for result in sorted_results:
            entity_type = result.entity_type
            entities_found.append(entity_type)
            start, end = result.start, result.end
            raw_fragment = text[start:end]

            if entity_type in self.REVERSIBLE_ENTITIES:
                # Replace with reversible token
                token = self.vault.tokenize(
                    owner=owner,
                    session_id=session_id,
                    entity_type=entity_type,
                    raw_value=raw_fragment,
                    workflow_id=workflow_id,
                )
                tokens_created[token] = raw_fragment
                sanitized_chars[start:end] = list(token)
            elif entity_type in self.IRREVERSIBLE_ENTITIES:
                # Mask irreversible PII
                replacement = "[private detail]"
                sanitized_chars[start:end] = list(replacement)

        sanitized_text = "".join(sanitized_chars)

        # Also redact any caller-supplied explicit private values
        for val in sorted((str(v) for v in private_values if v), key=len, reverse=True):
            if val and val in sanitized_text:
                sanitized_text = re.sub(
                    r"(?<!\w)" + re.escape(val) + r"(?!\w)",
                    "[private detail]",
                    sanitized_text,
                    flags=re.IGNORECASE,
                )

        return SanitizationResult(
            text=sanitized_text,
            tokens_created=tokens_created,
            secrets_detected=False,
            pii_entities_found=entities_found,
        )

    def verify_outbound(self, text: str, allowed_tokens: set[str] | None = None) -> None:
        """Fail-closed outbound verification before sending any payload to an external LLM.

        Raises PIILeakageError if unmasked credentials or un-tokenized sensitive identifiers are found.
        """
        if not isinstance(text, str):
            return

        results = self.analyzer.analyze(
            text=text,
            language="en",
            score_threshold=self.score_threshold,
        )

        for r in results:
            if r.entity_type in self.SECRET_ENTITIES:
                logger.error("Outbound PII check failed: Banking secret found in outbound text: %s", r.entity_type)
                raise PIILeakageError(f"Outbound payload contains unmasked secret: {r.entity_type}")

            raw_val = text[r.start:r.end]
            if allowed_tokens and raw_val in allowed_tokens:
                # Opaque token is permitted
                continue

            if r.entity_type in self.REVERSIBLE_ENTITIES or r.entity_type in self.IRREVERSIBLE_ENTITIES:
                # Check if it's already an opaque reference string
                if REFERENCE.fullmatch(raw_val):
                    continue
                logger.error("Outbound PII check failed: Sensitive entity %s found: %s", r.entity_type, raw_val[:3] + "***")
                raise PIILeakageError(f"Outbound payload contains sensitive PII: {r.entity_type}")

    def resolve_tokens(
        self,
        arguments: dict[str, Any],
        owner: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Resolve opaque tokens to raw values within the trusted execution boundary."""
        resolved = {}
        for key, value in arguments.items():
            if isinstance(value, str) and REFERENCE.fullmatch(value):
                detokenized = self.vault.detokenize(owner=owner, session_id=session_id, token=value)
                if detokenized is None:
                    raise ValueError(f"Token {value} could not be resolved or session isolation check failed")
                resolved[key] = detokenized
            elif isinstance(value, dict):
                resolved[key] = self.resolve_tokens(value, owner, session_id)
            else:
                resolved[key] = value
        return resolved

    def mask_value(self, value: Any) -> str:
        """Mask a sensitive value for safe display in confirmation or logs."""
        text = str(value)
        if len(text) <= 4:
            return "*" * len(text)
        return "*" * (len(text) - 4) + text[-4:]
