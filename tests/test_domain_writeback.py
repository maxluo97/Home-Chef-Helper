"""Tests for the write-back planning rules."""

from datetime import UTC, datetime, timedelta

from app.domain.writeback import plan_writeback

MONDAY = datetime(2026, 10, 19, 19, 0, tzinfo=UTC)
TUESDAY = MONDAY + timedelta(days=1)


def test_never_cooked_needs_nothing() -> None:
    """No cooks: nothing to send, even with the want-to-try tag."""
    plan = plan_writeback(None, None, 0, ["want-to-try"], None)

    assert not plan.is_needed


def test_first_cook_sets_last_made_and_clears_tag() -> None:
    """The first cook of a want-to-try recipe triggers both writes."""
    plan = plan_writeback(MONDAY, None, 1, ["want-to-try", "dinner"], None)

    assert plan.set_last_made_to == MONDAY
    assert plan.clear_want_to_try


def test_already_pushed_needs_nothing() -> None:
    """Once Mealie has the latest cook, there's nothing more to send."""
    plan = plan_writeback(TUESDAY, TUESDAY, 2, [], None)

    assert not plan.is_needed


def test_newer_cook_is_pushed() -> None:
    """A newer cook than what Mealie has is sent."""
    assert plan_writeback(TUESDAY, MONDAY, 2, [], None).set_last_made_to == TUESDAY


def test_deleting_latest_cook_moves_last_made_back() -> None:
    """If the latest cook is deleted, Mealie is set back to the previous one."""
    assert plan_writeback(MONDAY, TUESDAY, 1, [], None).set_last_made_to == MONDAY


def test_all_cooks_deleted_leaves_mealie_alone() -> None:
    """Mealie's last made can't be cleared through the API, so don't try."""
    assert not plan_writeback(None, TUESDAY, 0, [], None).is_needed


def test_tag_is_cleared_only_once() -> None:
    """If someone re-adds want-to-try after we cleared it, leave it be."""
    plan = plan_writeback(TUESDAY, TUESDAY, 3, ["want-to-try"], want_to_try_cleared_at=MONDAY)

    assert not plan.clear_want_to_try
