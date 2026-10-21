"""Encrypt small secrets (households' Mealie tokens) with the installation key.

One key for the whole installation, from the CHEF_SECRET_KEY env var
(plan §9.8). Fernet (AES + HMAC from the `cryptography` package) does the
encryption, so tampered or wrong-key ciphertext is detected, not silently
decrypted into garbage.
"""

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Shortest CHEF_SECRET_KEY accepted. 32 random characters is plenty; this
# mainly catches placeholders like "changeme".
MIN_SECRET_KEY_LENGTH = 32

# Labels the derived key with its purpose. Changing this string changes the
# key, so it must stay fixed once tokens are stored.
KEY_PURPOSE = b"chef-helper/mealie-token/v1"


class SecretDecryptionError(Exception):
    """Ciphertext couldn't be decrypted: wrong key, or data was altered."""


def derive_fernet_key(secret_key: str) -> bytes:
    """Turn any long secret string into the exact key format Fernet needs.

    Fernet wants 32 bytes, base64-encoded. HKDF stretches/condenses the
    secret into that, so CHEF_SECRET_KEY can be any random text (e.g. what
    HexOS's $RANDOM_STRING produces).
    """
    key_bytes = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=KEY_PURPOSE).derive(
        secret_key.encode("utf-8")
    )
    return base64.urlsafe_b64encode(key_bytes)


class SecretBox:
    """Encrypts and decrypts strings with one installation key."""

    def __init__(self, secret_key: str) -> None:
        if len(secret_key) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(f"CHEF_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters.")
        self._fernet = Fernet(derive_fernet_key(secret_key))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt to printable text, safe to store in a database column.

        Each call gives different output (random IV), even for the same input.
        """
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Reverse encrypt(). Raises SecretDecryptionError if it can't."""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as invalid_token:
            raise SecretDecryptionError(
                "Could not decrypt; CHEF_SECRET_KEY may have changed since this was stored."
            ) from invalid_token
