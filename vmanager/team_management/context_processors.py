from .access import can_access_team_features


def team_access(request):
    return {
        "can_access_team_features": can_access_team_features(request.user),
    }
