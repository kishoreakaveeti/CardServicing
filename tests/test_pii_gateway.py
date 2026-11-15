"""Tests for Presidio PII Gateway, Australian recognizers, and Token Vault isolation."""
import datetime
import pytest

from card_chat.pii.vault import InMemoryTokenVault
from card_chat.pii.recognizers import (
    validate_australian_tfn,
    validate_australian_medicare,
    validate_luhn,
)
from card_chat.pii.gateway import PIIGateway, PIILeakageError
from card_chat.model_context import PRIVATE_PASTE


def test_tfn_validation():
    # Valid 8-digit TFN
    # 10*1 + 7*2 + 8*3 + 4*4 + 6*5 + 3*6 + 5*7 + 1*1 = 10+14+24+16+30+18+35+1 = 148, not div by 11
    # Test valid TFNs
    # 8-digit TFN: 64 864 870 -> 6*10 + 4*7 + 8*8 + 6*4 + 4*6 + 8*3 + 7*5 + 0*1 = 60+28+64+24+24+24+35+0 = 259 (not 11)
    # A known valid 9-digit TFN: 123 456 782 -> 1*1 + 2*4 + 3*3 + 4*7 + 5*5 + 6*8 + 7*6 + 8*9 + 2*10
    # = 1 + 8 + 9 + 28 + 25 + 48 + 42 + 72 + 20 = 253. 253 / 11 = 23 (rem 0). VALID!
    assert validate_australian_tfn("123 456 782") is True
    assert validate_australian_tfn("123456782") is True
    assert validate_australian_tfn("123 456 783") is False
    assert validate_australian_tfn("invalid") is False


def test_medicare_validation():
    # Valid Medicare: 10 digits starting with 2-6
    # 2123 45678 1
    # weights: 1, 3, 7, 9, 1, 3, 7, 9
    # 2*1 + 1*3 + 2*7 + 3*9 + 4*1 + 5*3 + 6*7 + 7*9 = 2 + 3 + 14 + 27 + 4 + 15 + 42 + 63 = 170. 170 % 10 = 0.
    # So 9th digit should be 0.
    # Let's test check:
    assert validate_australian_medicare("2123456701") is True
    assert validate_australian_medicare("2123 45670 1") is True
    assert validate_australian_medicare("1123456701") is False  # doesn't start with 2-6
    assert validate_australian_medicare("2123456791") is False  # wrong check digit


def test_luhn_validation():
    # Standard test visa: 4111 1111 1111 1111
    assert validate_luhn("4111 1111 1111 1111") is True
    assert validate_luhn("4111111111111111") is True
    assert validate_luhn("4111111111111112") is False


def test_token_vault_session_and_customer_isolation():
    vault = InMemoryTokenVault(default_ttl_seconds=60)
    token = vault.tokenize(owner="cust1", session_id="sess1", entity_type="account", raw_value="12345678")
    assert token.startswith("selected_account_")

    # Same session & owner can detokenize
    assert vault.detokenize(owner="cust1", session_id="sess1", token=token) == "12345678"

    # Different owner CANNOT detokenize (strict customer isolation)
    assert vault.detokenize(owner="cust2", session_id="sess1", token=token) is None

    # Different session CANNOT detokenize (strict session isolation)
    assert vault.detokenize(owner="cust1", session_id="sess2", token=token) is None


def test_token_vault_ttl_and_expiry():
    vault = InMemoryTokenVault(default_ttl_seconds=1)
    token = vault.tokenize(owner="cust1", session_id="sess1", entity_type="account", raw_value="99998888", ttl_seconds=1)

    # Force expiration by adjusting created/expires
    entry = vault._by_token[token]
    entry.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)

    # Expired token resolves to None
    assert vault.detokenize(owner="cust1", session_id="sess1", token=token) is None
    # Expired token is cleaned up
    assert vault.cleanup_expired() == 0  # was cleaned on detokenize


def test_pii_gateway_sanitization_and_tokenization():
    vault = InMemoryTokenVault()
    gateway = PIIGateway(vault=vault)

    # Account number tokenization
    input_text = "Please transfer money from my account 123456789 to branch 062-000"
    res = gateway.sanitize_input(input_text, owner="cust1", session_id="sess1")

    assert "123456789" not in res.text
    assert "062-000" not in res.text
    assert "selected_account_" in res.text
    assert res.secrets_detected is False

    # Detokenization in trusted execution layer
    resolved = gateway.resolve_tokens({"acc": list(res.tokens_created.keys())[0]}, owner="cust1", session_id="sess1")
    assert "123456789" in str(resolved.values()) or "062-000" in str(resolved.values())


def test_pii_gateway_blocks_credentials_completely():
    vault = InMemoryTokenVault()
    gateway = PIIGateway(vault=vault)

    # Secrets (PIN, password, CVV, api_key) must be completely dropped
    secrets_inputs = [
        "My pin is 1234",
        "Here is my password: SuperSecretPassword123",
        "The cvv is 987",
        "My key is sk-ant-api03-abcdef1234567890",
    ]
    for text in secrets_inputs:
        res = gateway.sanitize_input(text, owner="cust1", session_id="sess1")
        assert res.secrets_detected is True
        assert res.text == PRIVATE_PASTE


def test_pii_gateway_verify_outbound_fail_closed():
    vault = InMemoryTokenVault()
    gateway = PIIGateway(vault=vault)

    # Safe text passes
    gateway.verify_outbound("Hello, how can I help you today?")

    # Opaque token passes
    token = vault.tokenize(owner="c", session_id="s", entity_type="acc", raw_value="12345678")
    gateway.verify_outbound(f"Selected account is {token}", allowed_tokens={token})

    # Raw account number fails outbound check
    with pytest.raises(PIILeakageError):
        gateway.verify_outbound("The customer account is 12345678")

    # Raw password fails outbound check
    with pytest.raises(PIILeakageError):
        gateway.verify_outbound("The password is secret123")
