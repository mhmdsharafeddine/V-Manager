from accounts.models import AccountProfile

from .access import can_access_advanced_analytics, can_access_team_features, get_team_role


def team_access(request):
    is_authenticated = getattr(request.user, "is_authenticated", False)
    role = get_team_role(request.user)
    can_view_roster_nav = bool(
        is_authenticated
        and can_access_team_features(request.user)
        and role in {
            AccountProfile.ROLE_MANAGER,
            AccountProfile.ROLE_COACH,
            AccountProfile.ROLE_STAFF,
        }
    )
    can_view_advanced_analytics = bool(
        not is_authenticated or can_access_advanced_analytics(request.user)
    )
    return {
        "can_access_team_features": can_access_team_features(request.user),
        "can_view_roster_nav": can_view_roster_nav,
        "can_view_match_readiness_nav": can_view_advanced_analytics,
        "can_view_ai_hub_nav": can_view_advanced_analytics,
    }
