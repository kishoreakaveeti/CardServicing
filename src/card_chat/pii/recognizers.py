"""Custom Microsoft Presidio recognizers for Australian banking and financial identifiers."""
import re
from typing import List, Optional

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts


def validate_australian_tfn(tfn_str: str) -> bool:
    """Validate an Australian Tax File Number using official ATO modulo 11 algorithm."""
    clean = "".join(c for c in tfn_str if c.isdigit())
    if not clean.isdigit():
        return False

    if len(clean) == 8:
        weights = [10, 7, 8, 4, 6, 3, 5, 1]
        total = sum(int(digit) * weight for digit, weight in zip(clean, weights))
        return total % 11 == 0
    elif len(clean) == 9:
        weights = [1, 4, 3, 7, 5, 8, 6, 9, 10]
        total = sum(int(digit) * weight for digit, weight in zip(clean, weights))
        return total % 11 == 0
    return False


def validate_australian_medicare(medicare_str: str) -> bool:
    """Validate an Australian Medicare number (10 digits)."""
    clean = "".join(c for c in medicare_str if c.isdigit())
    if not clean.isdigit() or len(clean) != 10:
        return False
    # First digit must be 2, 3, 4, 5, or 6
    if clean[0] not in "23456":
        return False
    weights = [1, 3, 7, 9, 1, 3, 7, 9]
    total = sum(int(clean[i]) * weights[i] for i in range(8))
    check_digit = total % 10
    return check_digit == int(clean[8])


def validate_luhn(card_str: str) -> bool:
    """Validate a card number using the Luhn algorithm."""
    clean = re.sub(r"[\s-]", "", card_str)
    if not clean.isdigit() or len(clean) < 13 or len(clean) > 19:
        return False
    digits = [int(c) for c in clean]
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(divmod(d * 2, 10))
    return checksum % 10 == 0


class AustralianBSBRecognizer(PatternRecognizer):
    """Recognizes Australian Bank State Branch (BSB) numbers (format: XXX-XXX or 6 digits)."""

    PATTERNS = [
        Pattern(
            "BSB (with hyphen/space)",
            r"(?i)\b(?:bsb|branch)[\s:]*([0-9]{3}[-\s][0-9]{3})\b",
            0.85,
        ),
        Pattern(
            "BSB (plain digits with prefix)",
            r"(?i)\b(?:bsb|branch)[\s:]*([0-9]{6})\b",
            0.75,
        ),
        Pattern(
            "BSB (standalone formatted)",
            r"\b[0-9]{3}-[0-9]{3}\b",
            0.5,
        ),
    ]

    CONTEXT = ["bsb", "branch", "account", "bank", "routing", "transfer", "pay", "deposit"]

    def __init__(self):
        super().__init__(
            supported_entity="AU_BSB",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="AustralianBSBRecognizer",
        )


class AustralianAccountNumberRecognizer(PatternRecognizer):
    """Recognizes Australian bank account numbers (6 to 10 digits)."""

    PATTERNS = [
        Pattern(
            "AU Account Number (with prefix)",
            r"(?i)\b(?:account|acct|acc|acc\s*no|account\s*number|account\s*no)(?:\s*(?:is|:|#|-|=)\s*|\s+)([0-9]{6,10})\b",
            0.85,
        ),
        Pattern(
            "AU Account Number in BSB context",
            r"(?i)\b[0-9]{3}[-\s][0-9]{3}[\s,]+(?:acc(?:ount)?(?:\s*(?:is|:|#|-|=)\s*|\s+))?([0-9]{6,10})\b",
            0.9,
        ),
        Pattern(
            "AU Account Number (common 6-9 digits)",
            r"(?i)\baccount\b\D{1,10}\b([0-9]{6,10})\b",
            0.8,
        ),
    ]

    CONTEXT = ["account", "acct", "bank", "deposit", "bsb", "number", "acc"]

    def __init__(self):
        super().__init__(
            supported_entity="AU_ACCOUNT_NUMBER",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="AustralianAccountNumberRecognizer",
        )


class AustralianTFNRecognizer(PatternRecognizer):
    """Recognizes Australian Tax File Numbers (TFN) with checksum verification."""

    PATTERNS = [
        Pattern(
            "AU TFN (with prefix)",
            r"(?i)\b(?:tfn|tax\s*file\s*number)[\s:]*([0-9]{3}[\s-]?[0-9]{3}[\s-]?[0-9]{2,3})\b",
            0.85,
        ),
        Pattern(
            "AU TFN (standard format)",
            r"\b[0-9]{3}[-\s][0-9]{3}[-\s][0-9]{2,3}\b",
            0.6,
        ),
    ]

    CONTEXT = ["tfn", "tax", "ato", "file", "number"]

    def __init__(self):
        super().__init__(
            supported_entity="AU_TFN",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="AustralianTFNRecognizer",
        )

    def validate_result(self, pattern_text: str) -> bool:
        return validate_australian_tfn(pattern_text)


class AustralianMedicareRecognizer(PatternRecognizer):
    """Recognizes Australian Medicare card numbers."""

    PATTERNS = [
        Pattern(
            "AU Medicare (with prefix)",
            r"(?i)\b(?:medicare|medicare\s*no|medicare\s*number)[\s:]*([2-6][0-9]{3}[\s-]?[0-9]{5}[\s-]?[0-9])\b",
            0.9,
        ),
        Pattern(
            "AU Medicare (standard format)",
            r"\b[2-6][0-9]{3}[-\s][0-9]{5}[-\s][0-9]\b",
            0.7,
        ),
    ]

    CONTEXT = ["medicare", "health", "green", "blue", "card"]

    def __init__(self):
        super().__init__(
            supported_entity="AU_MEDICARE",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="AustralianMedicareRecognizer",
        )

    def validate_result(self, pattern_text: str) -> bool:
        return validate_australian_medicare(pattern_text)


class AustralianPhoneNumberRecognizer(PatternRecognizer):
    """Recognizes Australian phone numbers (mobile 04xx and landlines)."""

    PATTERNS = [
        Pattern(
            "AU Mobile (+61 4xx)",
            r"\b(?:\+61|0)4[0-9]{2}[\s-]?[0-9]{3}[\s-]?[0-9]{3}\b",
            0.85,
        ),
        Pattern(
            "AU Landline (+61 2/3/7/8)",
            r"\b(?:\+?61[\s-]?)?\(?0?[2378]\)?[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b",
            0.75,
        ),
    ]

    CONTEXT = ["phone", "mobile", "tel", "call", "sms", "number", "contact"]

    def __init__(self):
        super().__init__(
            supported_entity="AU_PHONE_NUMBER",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="AustralianPhoneNumberRecognizer",
        )


class BankingCredentialsRecognizer(PatternRecognizer):
    """Recognizes authentication secrets, PINs, CVVs, passwords that must NEVER reach the LLM."""

    PATTERNS = [
        Pattern(
            "CVV/CVC",
            r"(?i)\b(?:cvv[2]?|cvc[2]?|security\s*code|card\s*verification)(?:\s*(?:is|:|=|-)\s*|\s+)([0-9]{3,4})\b",
            0.95,
        ),
        Pattern(
            "PIN",
            r"(?i)\b(?:pin|pin\s*code|passcode)(?:\s*(?:is|:|=|-)\s*|\s+)([0-9]{4,6})\b",
            0.95,
        ),
        Pattern(
            "Password / Secret / Token",
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|bearer\s+[a-zA-Z0-9._-]+)\b",
            0.95,
        ),
        Pattern(
            "Anthropic / OpenAI API Key",
            r"\b(?:sk-ant-[a-zA-Z0-9_-]+|sk-[a-zA-Z0-9_-]{20,})\b",
            0.99,
        ),
        Pattern(
            "Standalone CVV / PIN / Secret keywords",
            r"(?i)\b(?:cvv|cvc|pin|password|secret|credential)\b",
            0.85,
        ),
    ]

    CONTEXT = ["secret", "credential", "auth", "login", "password", "pin", "cvv", "key"]

    def __init__(self):
        super().__init__(
            supported_entity="BANKING_SECRET",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="BankingCredentialsRecognizer",
        )


class CreditCardNumberRecognizer(PatternRecognizer):
    """Recognizes 13-19 digit payment card numbers with Luhn check."""

    PATTERNS = [
        Pattern(
            "Card Number (formatted)",
            r"\b(?:\d{4}[-\s]){3}\d{4}\b",
            0.8,
        ),
        Pattern(
            "Card Number (unformatted with prefix)",
            r"(?i)\b(?:card|card\s*number|credit\s*card|debit\s*card)[\s:=]*(\d{13,19})\b",
            0.85,
        ),
    ]

    CONTEXT = ["card", "credit", "debit", "visa", "mastercard", "amex", "payment"]

    def __init__(self):
        super().__init__(
            supported_entity="CREDIT_CARD",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="CreditCardNumberRecognizer",
        )

    def validate_result(self, pattern_text: str) -> bool:
        return validate_luhn(pattern_text)
