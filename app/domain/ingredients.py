"""Rules for naming and classifying recipe ingredient lines."""

import re

# Mealie tag that marks recipes the household wants to try (plan §9.7).
WANT_TO_TRY_TAG_SLUG = "want-to-try"

# "optional", "optionally", "(optional)", "Optional:" ...
OPTIONAL_PATTERN = re.compile(r"\boptional(ly)?\b", re.IGNORECASE)


def canonical_ingredient_name(food_name: str | None) -> str | None:
    """Normalise a Mealie food name into the key used to match ingredients.

    Lower-cases, trims, and collapses inner whitespace, so "Baby  Spinach "
    and "baby spinach" are the same ingredient. Returns None for missing or
    blank names (lines Mealie couldn't parse, like "salt to taste").

    Phase 2 links these names to the canonical `ingredients` table.
    """
    if food_name is None:
        return None
    collapsed_name = " ".join(food_name.split()).lower()
    return collapsed_name or None


def is_optional_line(*line_texts: str | None) -> bool:
    """True if any of the line's texts (note, original text) says "optional"."""
    return any(line_text and OPTIONAL_PATTERN.search(line_text) for line_text in line_texts)


def is_want_to_try(tag_slugs: list[str]) -> bool:
    """True if the recipe carries the want-to-try tag."""
    return WANT_TO_TRY_TAG_SLUG in tag_slugs
