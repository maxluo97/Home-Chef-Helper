"""Create, join, list, and switch households; regenerate join codes (JSON API)."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentLoginSession, DbSession
from app.api.schemas import CreateHouseholdRequest, HouseholdOut, JoinHouseholdRequest
from app.services import accounts

router = APIRouter(prefix="/api/households", tags=["households"])

# Same message whether the household doesn't exist or isn't yours, so the
# API doesn't reveal which household IDs exist.
NOT_A_MEMBER_MESSAGE = "You are not a member of this household."


@router.get("")
def list_my_households(login_session: CurrentLoginSession, db: DbSession) -> list[HouseholdOut]:
    """Households the logged-in user belongs to."""
    households = accounts.list_households(db, login_session.user_id)
    return [HouseholdOut.model_validate(household) for household in households]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_household(
    payload: CreateHouseholdRequest, login_session: CurrentLoginSession, db: DbSession
) -> HouseholdOut:
    """Create a household; the creator joins it and it becomes active."""
    household = accounts.create_household(db, login_session, payload.name)
    return HouseholdOut.model_validate(household)


@router.post("/join")
def join_household(
    payload: JoinHouseholdRequest, login_session: CurrentLoginSession, db: DbSession
) -> HouseholdOut:
    """Join a household by its code; it becomes active on this device."""
    try:
        household = accounts.join_household(db, login_session, payload.join_code)
    except accounts.InvalidJoinCodeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No household has that join code.") from None
    return HouseholdOut.model_validate(household)


@router.post("/{household_id}/switch", status_code=status.HTTP_204_NO_CONTENT)
def switch_household(household_id: int, login_session: CurrentLoginSession, db: DbSession) -> None:
    """Make one of your other households the active one on this device."""
    try:
        accounts.switch_household(db, login_session, household_id)
    except accounts.NotAMemberError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_A_MEMBER_MESSAGE) from None


@router.post("/{household_id}/join-code")
def regenerate_join_code(
    household_id: int, login_session: CurrentLoginSession, db: DbSession
) -> HouseholdOut:
    """Issue a new join code; the old one stops working."""
    try:
        household = accounts.regenerate_join_code(db, login_session.user_id, household_id)
    except accounts.NotAMemberError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_A_MEMBER_MESSAGE) from None
    return HouseholdOut.model_validate(household)
