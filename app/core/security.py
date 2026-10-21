"""Password hashing, login-session tokens, and household join codes.

Pure helpers with no database access, so they can be tested in isolation.
"""

import hashlib
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# --- Passwords ---

# Argon2id with the library's recommended defaults (memory-hard, so
# brute-forcing a stolen database is expensive).
password_hasher = PasswordHasher()

# Hash of a throwaway password. Login checks it when the username doesn't
# exist, so "no such user" takes as long as "wrong password" and response
# times don't reveal which usernames exist.
TIMING_EQUALIZER_HASH = password_hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    """Return an Argon2 hash (salt included) suitable for storing."""
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True if `password` matches the stored hash."""
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True if the hash used weaker settings than the current defaults."""
    return password_hasher.check_needs_rehash(password_hash)


# --- Usernames ---

# Lower-case letters, digits, dot, underscore, hyphen; 3-32 characters.
USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{3,32}")


def normalize_username(raw_username: str) -> str:
    """Trim and lower-case a username, then check it's allowed.

    Usernames are case-insensitive ("Max" and "max" are the same person), so
    they're stored lower-case. Raises ValueError with a readable message.
    """
    username = raw_username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Username must be 3-32 characters: letters, digits, '.', '_' or '-'."
        )
    return username


# --- Login-session tokens ---

# Bytes of randomness in a session token (256 bits; unguessable).
SESSION_TOKEN_BYTES = 32


def new_session_token() -> str:
    """A fresh random token for the login cookie (URL-safe text)."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(session_token: str) -> str:
    """SHA-256 hex digest of a session token.

    Only the hash is stored, so someone who reads the database still can't
    log in as anyone. A fast hash is fine here because the token is long and
    random (unlike a password).
    """
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


# --- Household join codes ---

# Letters and digits that can't be confused when read aloud or typed on a
# phone: no 0/O, 1/I/L, or U/V.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ23456789"
JOIN_CODE_LENGTH = 8  # shown as XXXX-XXXX; 29^8 ≈ 5×10^11 possibilities
JOIN_CODE_GROUP_SIZE = 4


def generate_join_code() -> str:
    """A random, human-friendly join code like "K7QM-3XTW"."""
    code_characters = "".join(
        secrets.choice(JOIN_CODE_ALPHABET) for _iCharacter in range(JOIN_CODE_LENGTH)
    )
    return f"{code_characters[:JOIN_CODE_GROUP_SIZE]}-{code_characters[JOIN_CODE_GROUP_SIZE:]}"


def normalize_join_code(raw_join_code: str) -> str:
    """Put a typed join code into stored form, forgiving case and spacing.

    "k7qm 3xtw", "K7QM3XTW" and " K7QM-3XTW " all become "K7QM-3XTW".
    Returns the input cleaned up even if it isn't a valid code; the lookup
    simply won't find it.
    """
    code_characters = re.sub(r"[\s-]", "", raw_join_code).upper()
    if len(code_characters) != JOIN_CODE_LENGTH:
        return code_characters
    return f"{code_characters[:JOIN_CODE_GROUP_SIZE]}-{code_characters[JOIN_CODE_GROUP_SIZE:]}"
