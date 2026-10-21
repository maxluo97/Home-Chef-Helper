"""Tests for installation-key encryption of stored secrets."""

import pytest

from app.core.crypto import SecretBox, SecretDecryptionError

SECRET_KEY = "k" * 40
OTHER_SECRET_KEY = "z" * 40
MEALIE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.example-mealie-token"


def test_round_trip() -> None:
    """Encrypting then decrypting returns the original text."""
    secret_box = SecretBox(SECRET_KEY)

    assert secret_box.decrypt(secret_box.encrypt(MEALIE_TOKEN)) == MEALIE_TOKEN


def test_ciphertext_hides_the_token_and_varies() -> None:
    """Stored text doesn't contain the token and differs every time."""
    secret_box = SecretBox(SECRET_KEY)

    first_ciphertext = secret_box.encrypt(MEALIE_TOKEN)
    second_ciphertext = secret_box.encrypt(MEALIE_TOKEN)

    assert MEALIE_TOKEN not in first_ciphertext
    assert first_ciphertext != second_ciphertext


def test_same_key_works_across_instances() -> None:
    """A restart (new SecretBox, same key) can read what was stored before."""
    ciphertext = SecretBox(SECRET_KEY).encrypt(MEALIE_TOKEN)

    assert SecretBox(SECRET_KEY).decrypt(ciphertext) == MEALIE_TOKEN


def test_wrong_key_is_detected() -> None:
    """A changed CHEF_SECRET_KEY gives a clear error, not garbage."""
    ciphertext = SecretBox(SECRET_KEY).encrypt(MEALIE_TOKEN)

    with pytest.raises(SecretDecryptionError, match="CHEF_SECRET_KEY may have changed"):
        SecretBox(OTHER_SECRET_KEY).decrypt(ciphertext)


def test_tampered_ciphertext_is_detected() -> None:
    """Altering stored ciphertext makes decryption fail."""
    secret_box = SecretBox(SECRET_KEY)
    ciphertext = secret_box.encrypt(MEALIE_TOKEN)
    # Flip one character in the middle.
    middle_index = len(ciphertext) // 2
    replacement_character = "A" if ciphertext[middle_index] != "A" else "B"
    tampered_ciphertext = ciphertext[:middle_index] + replacement_character + ciphertext[middle_index + 1:]

    with pytest.raises(SecretDecryptionError):
        secret_box.decrypt(tampered_ciphertext)


def test_short_key_is_refused() -> None:
    """Placeholder-length keys are rejected up front."""
    with pytest.raises(ValueError, match="at least 32"):
        SecretBox("changeme")
