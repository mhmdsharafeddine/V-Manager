from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import AccountProfile

from .forms import TeamMemberForm
from .models import Team, TeamMembership

MANAGE_ROSTER_ROLES = {
    AccountProfile.ROLE_COACH,
    AccountProfile.ROLE_STAFF,
    AccountProfile.ROLE_MANAGER,
}
VIEW_ROSTER_ROLES = MANAGE_ROSTER_ROLES | {AccountProfile.ROLE_PLAYER}


def _get_role(user):
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", None)


def _default_member_title(role):
    return {
        AccountProfile.ROLE_COACH: "Head Coach",
        AccountProfile.ROLE_STAFF: "Staff",
        AccountProfile.ROLE_MANAGER: "Team Manager",
        AccountProfile.ROLE_PLAYER: "Player",
        AccountProfile.ROLE_PARENT: "Player",
    }.get(role, "Member")


def _badge_class(role, title):
    title_lower = (title or "").lower()
    if "captain" in title_lower:
        return "badge-purple"
    if role == AccountProfile.ROLE_COACH:
        return "badge-gold"
    if role == AccountProfile.ROLE_MANAGER:
        return "badge-amber"
    if role == AccountProfile.ROLE_STAFF:
        return "badge-orange"
    return "badge-blue"


def _permission_labels(role, title):
    title_lower = (title or "").lower()
    if role in {AccountProfile.ROLE_PLAYER, AccountProfile.ROLE_PARENT}:
        permissions = ["View Schedule", "Update Profile"]
        if "captain" in title_lower:
            permissions.append("View Team Stats")
        return permissions
    if role == AccountProfile.ROLE_COACH:
        return ["Manage Roster", "Post Announcements", "View Analytics"]
    if role == AccountProfile.ROLE_MANAGER:
        return ["Schedule Events", "Update Profile", "View Reports"]
    if role == AccountProfile.ROLE_STAFF:
        return ["View Schedule", "Update Profile", "Post Announcements"]
    return ["View Roster"]


def _can_manage_roster(user):
    team = _resolve_team_for_user(user)
    return getattr(user, "is_authenticated", False) and _get_role(user) in MANAGE_ROSTER_ROLES and team is not None


def _can_view_roster(user):
    if not getattr(user, "is_authenticated", False):
        return False
    role = _get_role(user)
    if role in VIEW_ROSTER_ROLES:
        return True
    return role == AccountProfile.ROLE_PARENT and getattr(user, "team_membership", None) is not None


def _get_or_create_team_for_manager(user):
    membership = getattr(user, "team_membership", None)
    if membership:
        return membership.team

    profile = getattr(user, "profile", None)
    base_name = (getattr(profile, "club_name", "") or "").strip()
    team_name = base_name or f"{user.get_full_name() or user.email.split('@')[0]}'s Team"
    team, _ = Team.objects.get_or_create(name=team_name, defaults={"created_by": user})
    TeamMembership.objects.get_or_create(
        user=user,
        defaults={
            "team": team,
            "member_title": _default_member_title(_get_role(user)),
            "added_by": user,
        },
    )
    return team


def _resolve_team_for_user(user):
    membership = getattr(user, "team_membership", None)
    if membership:
        return membership.team
    if _get_role(user) == AccountProfile.ROLE_MANAGER:
        return _get_or_create_team_for_manager(user)
    return None


def _serialize_membership(membership):
    user = membership.user
    profile = user.profile
    is_parent_player = profile.role == AccountProfile.ROLE_PARENT
    if is_parent_player:
        display_name = profile.child_name or user.get_full_name() or user.email
    else:
        display_name = user.get_full_name() or user.email
    subtitle = profile.position or ("Player" if is_parent_player else profile.get_role_display())
    return {
        "membership": membership,
        "user": user,
        "profile": profile,
        "avatar_url": profile.profile_photo.url if profile.profile_photo else "",
        "initials": profile.initials,
        "display_name": display_name,
        "display_title": membership.member_title or profile.get_role_display(),
        "badge_class": _badge_class(profile.role, membership.member_title),
        "status_label": "Active" if membership.is_active else "Inactive",
        "permissions": _permission_labels(profile.role, membership.member_title),
        "subtitle": subtitle,
    }


def _summary_cards(team):
    memberships = team.memberships.select_related("user__profile").filter(is_active=True)
    players = sum(
        1
        for membership in memberships
        if membership.user.profile.role in {AccountProfile.ROLE_PLAYER, AccountProfile.ROLE_PARENT}
    )
    captains = sum(1 for membership in memberships if "captain" in (membership.member_title or "").lower())
    coaches = sum(1 for membership in memberships if membership.user.profile.role == AccountProfile.ROLE_COACH)
    staff_and_managers = sum(
        1
        for membership in memberships
        if membership.user.profile.role in {AccountProfile.ROLE_STAFF, AccountProfile.ROLE_MANAGER}
    )
    return [
        {
            "label": "Players",
            "count": players,
            "class_name": "summary-card-blue",
            "caption": "Active members",
        },
        {
            "label": "Team Captains",
            "count": captains,
            "class_name": "summary-card-purple",
            "caption": "Leadership roles",
        },
        {
            "label": "Coaches",
            "count": coaches,
            "class_name": "summary-card-gold",
            "caption": "Coaching staff",
        },
        {
            "label": "Staff & Managers",
            "count": staff_and_managers,
            "class_name": "summary-card-red",
            "caption": "Support members",
        },
    ]


def _base_team_context(*, request, team):
    query = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "all").strip().lower()
    if status_filter not in {"all", "active", "inactive"}:
        status_filter = "all"
    memberships = team.memberships.select_related("user__profile")
    if status_filter == "active":
        memberships = memberships.filter(is_active=True)
    elif status_filter == "inactive":
        memberships = memberships.filter(is_active=False)
    if query:
        memberships = memberships.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(member_title__icontains=query)
            | Q(user__profile__position__icontains=query)
        )
    recent_members = team.memberships.select_related("user__profile").order_by("-joined_at")[:3]
    return {
        "team": team,
        "query": query,
        "status_filter": status_filter,
        "members": [_serialize_membership(membership) for membership in memberships],
        "summary_cards": _summary_cards(team),
        "recent_members": [_serialize_membership(membership) for membership in recent_members],
        "can_manage": _can_manage_roster(request.user),
    }


@login_required
def roster_view(request):
    if not _can_view_roster(request.user):
        messages.error(request, "You do not have access to the team roster.")
        return redirect("home")

    team = _resolve_team_for_user(request.user)
    can_manage = request.user.is_authenticated and _get_role(request.user) in MANAGE_ROSTER_ROLES and team is not None
    context = {"team": team, "can_manage": can_manage, "members": [], "summary_cards": [], "query": "", "status_filter": "all"}
    if team is None:
        messages.info(request, "You are not assigned to a team yet.")
    else:
        context.update(_base_team_context(request=request, team=team))
    return render(request, "team_management/roster.html", context)


@login_required
def add_member_view(request):
    if _get_role(request.user) not in MANAGE_ROSTER_ROLES:
        messages.error(request, "Only coaches, staff, and managers can manage the roster.")
        return redirect("team_management:roster")

    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before you can add members to its roster.")
        return redirect("team_management:roster")

    form = TeamMemberForm(request.POST or None, request.FILES or None, team=team)
    if request.method == "POST" and form.is_valid():
        membership = form.save(team=team, added_by=request.user)
        messages.success(request, f"{membership.user.get_full_name() or membership.user.email} was added to the roster.")
        return redirect("team_management:roster")

    context = _base_team_context(request=request, team=team)
    context.update(
        {
            "form": form,
            "page_title": "Adding Members",
            "page_description": "Add new players and staff to your team with the right roles in just a few clicks.",
            "submit_label": "Add Member",
            "mode": "add",
        }
    )
    return render(request, "team_management/member_form.html", context)


@login_required
def edit_member_view(request, membership_id):
    if _get_role(request.user) not in MANAGE_ROSTER_ROLES:
        messages.error(request, "Only coaches, staff, and managers can manage the roster.")
        return redirect("team_management:roster")

    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before you can edit its roster.")
        return redirect("team_management:roster")

    membership = get_object_or_404(
        TeamMembership.objects.select_related("user__profile", "team"),
        pk=membership_id,
        team=team,
    )

    form = TeamMemberForm(
        request.POST or None,
        request.FILES or None,
        team=team,
        membership=membership,
    )
    if request.method == "POST" and form.is_valid():
        form.save(team=team, added_by=request.user)
        messages.success(request, "Member details were updated successfully.")
        return redirect("team_management:roster")

    context = _base_team_context(request=request, team=team)
    context.update(
        {
            "form": form,
            "page_title": "Editing Member",
            "page_description": "Update a roster member's information, team role, and player details.",
            "submit_label": "Save Changes",
            "mode": "edit",
            "editing_member": _serialize_membership(membership),
        }
    )
    return render(request, "team_management/member_form.html", context)
