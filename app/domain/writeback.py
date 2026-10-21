"""Decide what Chef Helper should write back to Mealie for one recipe.

Pure rules (no I/O), applied by app/services/mealie_writeback.py:
- Mealie's "last made" should equal the household's most recent cook.
  Mealie accepts any time, so we always send the latest ourselves; that
  way a back-dated cook never moves it backwards, and deleting the latest
  cook moves it back to the previous one.
- After a recipe's first cook, remove its want-to-try tag once (plan §9.7).
"""

from dataclasses import dataclass
from datetime import datetime

from app.domain.ingredients import WANT_TO_TRY_TAG_SLUG


@dataclass(frozen=True)
class WritebackPlan:
    """What to send to Mealie for one recipe (nothing if both are empty)."""

    set_last_made_to: datetime | None = None
    clear_want_to_try: bool = False

    @property
    def is_needed(self) -> bool:
        """True if anything must be written."""
        return self.set_last_made_to is not None or self.clear_want_to_try


def plan_writeback(
    latest_cook_at: datetime | None,
    last_made_pushed: datetime | None,
    cook_count: int,
    tag_slugs: list[str],
    want_to_try_cleared_at: datetime | None,
) -> WritebackPlan:
    """Compare the cook log with what was last sent, and plan the difference.

    If every cook was deleted (latest_cook_at is None), "last made" is left
    as it is: Mealie's API can set it but not clear it.
    """
    last_made_is_stale = latest_cook_at is not None and latest_cook_at != last_made_pushed
    tag_needs_clearing = (
        cook_count > 0 and WANT_TO_TRY_TAG_SLUG in tag_slugs and want_to_try_cleared_at is None
    )
    return WritebackPlan(
        set_last_made_to=latest_cook_at if last_made_is_stale else None,
        clear_want_to_try=tag_needs_clearing,
    )
