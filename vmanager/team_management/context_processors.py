from accounts.models import AccountProfile

from .access import can_access_team_features, get_team_role


def team_access(request):
    role = get_team_role(request.user)
    can_view_roster_nav = bool(
        getattr(request.user, "is_authenticated", False)
        and can_access_team_features(request.user)
        and role in {
            AccountProfile.ROLE_MANAGER,
            AccountProfile.ROLE_COACH,
            AccountProfile.ROLE_STAFF,
        }
    )
    return {
        "can_access_team_features": can_access_team_features(request.user),
        "can_view_roster_nav": can_view_roster_nav,
    }
