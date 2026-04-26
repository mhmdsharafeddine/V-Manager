from accounts.models import AccountProfile


def get_team_role(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", None)


def get_team_membership(user):
    if not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "team_membership", None)


def is_inactive_team_member(user):
    membership = get_team_membership(user)
    return membership is not None and not membership.is_active


def can_access_team_features(user):
    if not getattr(user, "is_authenticated", False):
        return False

    membership = get_team_membership(user)
    if membership is not None:
        return membership.is_active

    return get_team_role(user) in {
        AccountProfile.ROLE_COACH,
        AccountProfile.ROLE_STAFF,
        AccountProfile.ROLE_MANAGER,
    }


def can_access_advanced_analytics(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and can_access_team_features(user)
        and get_team_role(user) in {
            AccountProfile.ROLE_COACH,
            AccountProfile.ROLE_STAFF,
            AccountProfile.ROLE_MANAGER,
        }
    )
