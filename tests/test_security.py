"""Tests for password, username, session-token, and join-code helpers."""

import re

import pytest

from app.core.security import (
    JOIN_CODE_ALPHABET,
    generate_join_code,
    hash_password,
    hash_session_token,
    new_session_token,
    normalize_join_code,
    normalize_username,
    verify_password,
)

# --- Passwords ---


def test_password_hash_verifies_only_the_right_password() -> None:
    """The hash accepts the original password and rejects others."""
    stored_hash = hash_password("correct horse battery staple")

    assert verify_password(stored_hash, "correct horse battery staple")
    assert not verify_password(stored_hash, "Correct horse battery staple")


def test_password_hashes_are_salted() -> None:
    """The same password hashes differently each time (random salt)."""
    assert hash_password("same password") != hash_password("same password")


def test_garbage_hash_is_a_failed_check_not_a_crash() -> None:
    """A corrupted stored hash makes verification return False."""
    assert not verify_password("not-a-real-hash", "anything")


# --- Usernames ---


@pytest.mark.parametrize(
    ("raw_username", "expected"),
    [("max", "max"), ("  Max.Luo ", "max.luo"), ("chef_2-b", "chef_2-b")],
)
def test_username_is_trimmed_and_lowercased(raw_username: str, expected: str) -> None:
    """Allowed usernames are normalised to their stored form."""
    assert normalize_username(raw_username) == expected


@pytest.mark.parametrize("raw_username", ["ab", "x" * 33, "has space", "emoji🍳", "semi;colon", ""])
def test_bad_usernames_are_rejected(raw_username: str) -> None:
    """Too short, too long, or disallowed characters raise ValueError."""
    with pytest.raises(ValueError, match="Username must be"):
        normalize_username(raw_username)


# --- Session tokens ---


def test_session_tokens_are_unique_and_long() -> None:
    """Tokens don't repeat and are long enough to be unguessable."""
    session_tokens = {new_session_token() for _iToken in range(100)}

    assert len(session_tokens) == 100
    assert all(len(session_token) >= 40 for session_token in session_tokens)


def test_session_token_hash_is_stable_and_not_the_token() -> None:
    """The stored hash is deterministic and differs from the token itself."""
    session_token = new_session_token()

    assert hash_session_token(session_token) == hash_session_token(session_token)
    assert hash_session_token(session_token) != session_token
    assert len(hash_session_token(session_token)) == 64


# --- Join codes ---


def test_join_code_format() -> None:
    """Codes look like XXXX-XXXX using only the unambiguous alphabet."""
    allowed = re.escape(JOIN_CODE_ALPHABET)
    for _iCode in range(50):
        assert re.fullmatch(f"[{allowed}]{{4}}-[{allowed}]{{4}}", generate_join_code())


@pytest.mark.parametrize("typed_code", ["k7qm 3xtw", "K7QM3XTW", " K7QM-3XTW ", "k7qm-3xtw"])
def test_join_code_normalization_forgives_typing(typed_code: str) -> None:
    """Case, spaces, and a missing hyphen don't matter."""
    assert normalize_join_code(typed_code) == "K7QM-3XTW"


def test_wrong_length_join_code_is_left_unmatched() -> None:
    """A code of the wrong length is cleaned but not reformatted."""
    assert normalize_join_code("abc") == "ABC"
