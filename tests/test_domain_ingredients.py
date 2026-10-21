"""Tests for ingredient naming and classification rules."""

import pytest

from app.domain.ingredients import canonical_ingredient_name, is_optional_line, is_want_to_try


@pytest.mark.parametrize(
    ("food_name", "expected"),
    [
        ("spinach", "spinach"),
        ("  Baby  Spinach ", "baby spinach"),
        ("Parmesan Cheese", "parmesan cheese"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_canonical_ingredient_name(food_name: str | None, expected: str | None) -> None:
    """Names are trimmed, lower-cased, and whitespace-collapsed; blanks are None."""
    assert canonical_ingredient_name(food_name) == expected


@pytest.mark.parametrize(
    ("line_texts", "expected"),
    [
        (("optional", None), True),
        ((None, "1 tsp chili flakes (optional)"), True),
        (("Optional: for garnish", ""), True),
        (("optionally toasted", None), True),
        (("shredded", "2 cups shredded mozzarella"), False),
        ((None, None), False),
        (("nonoptional-ish word", None), False),  # whole word only
    ],
)
def test_is_optional_line(line_texts: tuple[str | None, ...], expected: bool) -> None:
    """Lines mentioning "optional" in any text field are optional."""
    assert is_optional_line(*line_texts) is expected


def test_is_want_to_try() -> None:
    """Only the exact want-to-try tag slug counts."""
    assert is_want_to_try(["dinner", "want-to-try"])
    assert not is_want_to_try(["want-to-try-later", "dinner"])
    assert not is_want_to_try([])
