from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.email_utils import send_invited_account_email, send_membership_rejection_email
from accounts.models import AccountProfile

from .access import can_access_team_features, get_team_role, is_inactive_team_member
from .forms import RosterAccountInviteForm, TeamMemberForm
from .models import Team, TeamMembership

MANAGE_ROSTER_ROLES = {
    AccountProfile.ROLE_COACH,
    AccountProfile.ROLE_STAFF,
    AccountProfile.ROLE_MANAGER,
}
CREATE_ROSTER_ROLES = {
    AccountProfile.ROLE_STAFF,
    AccountProfile.ROLE_MANAGER,
}
DELETE_ROSTER_ROLES = {
    AccountProfile.ROLE_STAFF,
    AccountProfile.ROLE_MANAGER,
}
VIEW_ROSTER_ROLES = MANAGE_ROSTER_ROLES | {
    AccountProfile.ROLE_PLAYER,
    AccountProfile.ROLE_PARENT,
}
REVIEW_ASSIGNABLE_ROLES = {
    AccountProfile.ROLE_PLAYER,
    AccountProfile.ROLE_PARENT,
    AccountProfile.ROLE_COACH,
    AccountProfile.ROLE_STAFF,
}


def _get_role(user):
    return get_team_role(user)


def _default_member_title(role):
    return {
        AccountProfile.ROLE_COACH: "Head Coach",
        AccountProfile.ROLE_STAFF: "Staff",
        AccountProfile.ROLE_MANAGER: "Team Manager",
        AccountProfile.ROLE_PLAYER: "Player",
        AccountProfile.ROLE_PARENT: "Parent",
    }.get(role, "Member")


def _badge_class(role, title):
    title_lower = (title or "").lower()
    if "captain" in title_lower:
        return "badge-purple"
    if role == AccountProfile.ROLE_PARENT:
        return "badge-pink"
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


def _member_feedback_label(membership):
    role = membership.user.profile.role
    if role in {AccountProfile.ROLE_PLAYER, AccountProfile.ROLE_PARENT}:
        return "Player"
    return membership.user.profile.get_role_display()


def _can_manage_roster(user):
    team = _resolve_team_for_user(user)
    return can_access_team_features(user) and _get_role(user) in MANAGE_ROSTER_ROLES and team is not None


def _can_invite_members(user):
    team = _resolve_team_for_user(user)
    return can_access_team_features(user) and _get_role(user) in CREATE_ROSTER_ROLES and team is not None


def _can_edit_member(user, membership):
    if not getattr(user, "is_authenticated", False):
        return False
    if not can_access_team_features(user):
        return False

    actor_role = _get_role(user)
    if actor_role not in MANAGE_ROSTER_ROLES:
        return False

    actor_team = _resolve_team_for_user(user)
    if actor_team is None or membership.team_id != actor_team.id:
        return False

    if actor_role == AccountProfile.ROLE_COACH:
        return membership.user.profile.role in {
            AccountProfile.ROLE_PLAYER,
            AccountProfile.ROLE_PARENT,
        }

    return True


def _can_delete_member(user, membership):
    if not getattr(user, "is_authenticated", False):
        return False
    if not can_access_team_features(user):
        return False
    actor_role = _get_role(user)
    if actor_role not in DELETE_ROSTER_ROLES:
        return False
    actor_team = _resolve_team_for_user(user)
    if actor_team is None or membership.team_id != actor_team.id:
        return False
    if membership.user_id == user.id:
        return False
    target_role = membership.user.profile.role
    if actor_role == AccountProfile.ROLE_STAFF and target_role == AccountProfile.ROLE_MANAGER:
        return False
    return True


def _can_view_roster(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if not can_access_team_features(user):
        return False
    role = _get_role(user)
    return role in VIEW_ROSTER_ROLES


def _inactive_access_response(request):
    messages.error(
        request,
        "Your team access is inactive. Contact a manager or staff member to reactivate your access.",
    )
    return redirect("home")


def _access_denied_redirect(user):
    if _can_view_roster(user):
        return redirect("team_management:roster")
    return redirect("home")


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


def _serialize_membership(membership, *, viewer=None):
    user = membership.user
    profile = user.profile
    display_name = user.get_full_name() or user.email
    subtitle = profile.position or profile.get_role_display()
    if membership.status == TeamMembership.STATUS_PENDING:
        status_label = "Pending Approval"
    elif membership.status == TeamMembership.STATUS_REJECTED:
        status_label = "Rejected"
    else:
        status_label = "Active" if membership.is_active else "Inactive"

    return {
        "membership": membership,
        "user": user,
        "profile": profile,
        "avatar_url": profile.profile_photo.url if profile.profile_photo else "",
        "initials": profile.initials,
        "display_name": display_name,
        "display_title": "Parent" if profile.role == AccountProfile.ROLE_PARENT else (membership.member_title or profile.get_role_display()),
        "badge_class": _badge_class(profile.role, membership.member_title),
        "status_label": status_label,
        "requested_role_label": _requested_role_label(membership.requested_role),
        "permissions": _permission_labels(profile.role, membership.member_title),
        "subtitle": subtitle,
        "can_edit": _can_edit_member(viewer, membership) if viewer is not None else False,
        "can_delete": _can_delete_member(viewer, membership) if viewer is not None else False,
    }


def _requested_role_label(role_value):
    return dict(AccountProfile.ROLE_CHOICES).get(role_value, (role_value or "member").title())


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


def _apply_member_type_filter(memberships, member_type_filter):
    if member_type_filter == "players":
        return memberships.filter(user__profile__role=AccountProfile.ROLE_PLAYER)
    if member_type_filter == "parents":
        return memberships.filter(user__profile__role=AccountProfile.ROLE_PARENT)
    if member_type_filter == "captains":
        return memberships.filter(member_title__icontains="captain")
    if member_type_filter == "coaches":
        return memberships.filter(user__profile__role=AccountProfile.ROLE_COACH)
    if member_type_filter == "staff":
        return memberships.filter(user__profile__role=AccountProfile.ROLE_STAFF)
    if member_type_filter == "managers":
        return memberships.filter(user__profile__role=AccountProfile.ROLE_MANAGER)
    return memberships


def _apply_sort(memberships, sort_filter):
    if sort_filter == "newest":
        return memberships.order_by("-joined_at", "user__first_name", "user__last_name", "user__email")
    if sort_filter == "oldest":
        return memberships.order_by("joined_at", "user__first_name", "user__last_name", "user__email")
    return memberships.order_by("user__first_name", "user__last_name", "user__email")


def _base_team_context(*, request, team):
    query = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "all").strip().lower()
    sort_filter = (request.GET.get("sort") or "alphabetical").strip().lower()
    member_type_filter = (request.GET.get("member_type") or "all").strip().lower()
    if status_filter not in {"all", "active", "inactive"}:
        status_filter = "all"
    if sort_filter not in {"alphabetical", "newest", "oldest"}:
        sort_filter = "alphabetical"
    if member_type_filter not in {"all", "players", "parents", "captains", "coaches", "staff", "managers"}:
        member_type_filter = "all"
    memberships = team.memberships.select_related("user__profile")
    memberships = memberships.exclude(status=TeamMembership.STATUS_PENDING)
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
    memberships = _apply_member_type_filter(memberships, member_type_filter)
    memberships = _apply_sort(memberships, sort_filter)
    recent_members = team.memberships.select_related("user__profile").order_by("-joined_at")[:3]
    pending_memberships = []
    if _can_manage_roster(request.user):
        pending_memberships = list(
            team.memberships.select_related("user__profile")
            .filter(status=TeamMembership.STATUS_PENDING)
            .order_by("joined_at", "user__email")
        )

    return {
        "team": team,
        "query": query,
        "status_filter": status_filter,
        "sort_filter": sort_filter,
        "member_type_filter": member_type_filter,
        "members": [_serialize_membership(membership, viewer=request.user) for membership in memberships],
        "summary_cards": _summary_cards(team),
        "recent_members": [_serialize_membership(membership, viewer=request.user) for membership in recent_members],
        "pending_requests": [_serialize_membership(membership, viewer=request.user) for membership in pending_memberships],
        "review_assignable_roles": [
            (role, label)
            for role, label in AccountProfile.ROLE_CHOICES
            if role in REVIEW_ASSIGNABLE_ROLES
        ],
        "can_manage": _can_manage_roster(request.user),
    }


def _pending_linked_child_users_for_parent_membership(membership):
    profile = getattr(membership.user, "profile", None)
    if not profile or profile.role != AccountProfile.ROLE_PARENT:
        return []

    child_users = []
    for child_profile in profile.linked_children_profiles():
        child_user = getattr(child_profile, "user", None)
        child_membership = getattr(child_user, "team_membership", None) if child_user else None
        if (
            child_user
            and child_membership
            and child_membership.team_id == membership.team_id
            and child_membership.status in {TeamMembership.STATUS_PENDING, TeamMembership.STATUS_REJECTED}
        ):
            child_users.append(child_user)
    return child_users


def _linked_child_memberships_for_parent_membership(membership):
    profile = getattr(membership.user, "profile", None)
    if not profile or profile.role != AccountProfile.ROLE_PARENT:
        return []

    child_memberships = []
    for child_profile in profile.linked_children_profiles():
        child_user = getattr(child_profile, "user", None)
        child_membership = getattr(child_user, "team_membership", None) if child_user else None
        if (
            child_membership
            and child_membership.team_id == membership.team_id
            and child_membership.pk != membership.pk
        ):
            child_memberships.append(child_membership)
    return child_memberships


@login_required
def roster_view(request):
    if not _can_view_roster(request.user):
        if is_inactive_team_member(request.user):
            return _inactive_access_response(request)
        messages.error(request, "You do not have access to the team roster.")
        return redirect("home")

    team = _resolve_team_for_user(request.user)
    can_manage = request.user.is_authenticated and _get_role(request.user) in MANAGE_ROSTER_ROLES and team is not None
    can_invite = request.user.is_authenticated and _get_role(request.user) in CREATE_ROSTER_ROLES and team is not None
    context = {
        "team": team,
        "can_manage": can_manage,
        "can_invite": can_invite,
        "members": [],
        "pending_requests": [],
        "summary_cards": [],
        "query": "",
        "status_filter": "all",
        "sort_filter": "alphabetical",
        "member_type_filter": "all",
        "review_assignable_roles": [
            (role, label)
            for role, label in AccountProfile.ROLE_CHOICES
            if role in REVIEW_ASSIGNABLE_ROLES
        ],
    }
    if team is None:
        messages.info(request, "You are not assigned to a team yet.")
    else:
        context.update(_base_team_context(request=request, team=team))
        context["can_invite"] = can_invite
    return render(request, "team_management/roster.html", context)


@login_required
def invite_member_view(request):
    if is_inactive_team_member(request.user):
        return _inactive_access_response(request)
    if _get_role(request.user) not in CREATE_ROSTER_ROLES:
        messages.error(request, "Only staff and managers can create roster accounts.")
        return _access_denied_redirect(request.user)

    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before you can create roster accounts.")
        return _access_denied_redirect(request.user)

    form = RosterAccountInviteForm(
        request.POST or None,
        request.FILES or None,
        actor=request.user,
        team=team,
        prefix="invite",
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                membership, temporary_password = form.save(team=team, added_by=request.user)
                send_invited_account_email(
                    user=membership.user,
                    temporary_password=temporary_password,
                    team_name=team.name,
                    inviter_name=request.user.get_full_name() or request.user.email,
                )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field_name, field_errors in exc.message_dict.items():
                    for error in field_errors:
                        form.add_error(field_name if field_name in form.fields else None, error)
            else:
                for error in exc.messages:
                    form.add_error(None, error)
        except Exception as exc:
            form.add_error(None, f"We could not send the invite email right now. {exc}")
        else:
            messages.success(
                request,
                f"{membership.user.get_full_name() or membership.user.email} was added to the roster and emailed a temporary password.",
            )
            return redirect("team_management:roster")

    context = _base_team_context(request=request, team=team)
    context.update(
        {
            "form": form,
            "page_title": "Create Roster Account",
            "page_description": "Create player, parent, coach, and staff accounts for your team from one focused workspace.",
            "submit_label": "Create Account & Send Email",
            "mode": "invite",
            "can_manage": True,
        }
    )
    return render(request, "team_management/invite_member.html", context)


@login_required
def add_member_view(request):
    if is_inactive_team_member(request.user):
        return _inactive_access_response(request)
    if _get_role(request.user) not in CREATE_ROSTER_ROLES:
        messages.error(request, "Only staff and managers can add roster members.")
        return _access_denied_redirect(request.user)

    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before you can add members to its roster.")
        return _access_denied_redirect(request.user)

    form = TeamMemberForm(
        request.POST or None,
        request.FILES or None,
        team=team,
        actor_role=_get_role(request.user),
    )
    if request.method == "POST" and form.is_valid():
        membership = form.save(team=team, added_by=request.user)
        messages.success(request, f"{_member_feedback_label(membership)} added.")
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
    if is_inactive_team_member(request.user):
        return _inactive_access_response(request)
    if _get_role(request.user) not in MANAGE_ROSTER_ROLES:
        messages.error(request, "Only coaches, staff, and managers can manage the roster.")
        return _access_denied_redirect(request.user)

    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before you can edit its roster.")
        return _access_denied_redirect(request.user)

    membership = (
        TeamMembership.objects.select_related("user__profile", "team")
        .filter(pk=membership_id, team=team)
        .first()
    )
    if membership is None:
        messages.error(request, "This roster member was already removed.")
        return redirect("team_management:roster")

    if not _can_edit_member(request.user, membership):
        if _get_role(request.user) == AccountProfile.ROLE_COACH:
            messages.error(request, "Coaches can only edit player roster members.")
        else:
            messages.error(request, "You do not have permission to edit this roster member.")
        return _access_denied_redirect(request.user)

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
            "editing_member": _serialize_membership(membership, viewer=request.user),
        }
    )
    return render(request, "team_management/member_form.html", context)


@login_required
@require_POST
def delete_member_view(request, membership_id):
    if is_inactive_team_member(request.user):
        return _inactive_access_response(request)
    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before you can remove roster members.")
        return _access_denied_redirect(request.user)

    membership = (
        TeamMembership.objects.select_related("user__profile", "team")
        .filter(pk=membership_id, team=team)
        .first()
    )
    if membership is None:
        messages.error(request, "This roster member was already removed.")
        return redirect("team_management:roster")

    actor_role = _get_role(request.user)
    if actor_role not in DELETE_ROSTER_ROLES:
        messages.error(request, "Only managers and staff can remove roster members.")
        return _access_denied_redirect(request.user)

    if membership.user_id == request.user.id:
        messages.error(request, "You cannot remove your own roster access.")
        return redirect("team_management:roster")

    if actor_role == AccountProfile.ROLE_STAFF and membership.user.profile.role == AccountProfile.ROLE_MANAGER:
        messages.error(request, "Staff members cannot remove managers from the roster.")
        return redirect("team_management:roster")

    feedback_label = _member_feedback_label(membership)
    linked_child_memberships = _linked_child_memberships_for_parent_membership(membership)

    with transaction.atomic():
        for child_membership in linked_child_memberships:
            child_membership.delete()
        membership.delete()

    if linked_child_memberships:
        child_count = len(linked_child_memberships)
        messages.success(
            request,
            f"{feedback_label} deleted. {child_count} linked child roster entr{'ies' if child_count != 1 else 'y'} were also removed.",
        )
    else:
        messages.success(request, f"{feedback_label} deleted.")
    return redirect("team_management:roster")


@login_required
@require_POST
def review_membership_request_view(request, membership_id):
    if is_inactive_team_member(request.user):
        return _inactive_access_response(request)
    if _get_role(request.user) not in MANAGE_ROSTER_ROLES:
        messages.error(request, "Only coaches, staff, and managers can review requests.")
        return _access_denied_redirect(request.user)

    team = _resolve_team_for_user(request.user)
    if team is None:
        messages.error(request, "You must belong to a team before reviewing requests.")
        return _access_denied_redirect(request.user)

    membership = (
        TeamMembership.objects.select_related("user__profile", "team")
        .filter(pk=membership_id, team=team)
        .first()
    )
    if membership is None:
        messages.error(request, "This membership request was already removed.")
        return redirect("team_management:roster")

    action = (request.POST.get("action") or "").strip().lower()
    if membership.status not in {TeamMembership.STATUS_PENDING, TeamMembership.STATUS_REJECTED}:
        messages.error(request, "This request is no longer pending review.")
        return redirect("team_management:roster")

    if action == "approve":
        approved_role = (request.POST.get("approved_role") or "").strip()
        if approved_role not in REVIEW_ASSIGNABLE_ROLES:
            messages.error(request, "Please choose a valid role for approval.")
            return redirect("team_management:roster")

        membership.user.profile.role = approved_role
        membership.user.profile.save(update_fields=["role"])

        membership.status = TeamMembership.STATUS_APPROVED
        membership.is_active = True
        membership.member_title = _default_member_title(approved_role)
        membership.rejection_reason = ""
        membership.reviewed_by = request.user
        membership.reviewed_at = timezone.now()
        membership.save(
            update_fields=[
                "status",
                "is_active",
                "member_title",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
            ]
        )
        messages.success(request, "Membership request approved.")
        return redirect("team_management:roster")

    if action == "reject":
        reason = (request.POST.get("rejection_reason") or "").strip()
        user_to_delete = membership.user
        team_name = membership.team.name
        linked_child_users = _pending_linked_child_users_for_parent_membership(membership)

        users_to_delete = [user_to_delete, *[user for user in linked_child_users if user.pk != user_to_delete.pk]]
        child_count = max(0, len(users_to_delete) - 1)

        for rejected_user in users_to_delete:
            try:
                send_membership_rejection_email(rejected_user, team_name, reason)
            except Exception as e:
                messages.warning(request, f"Request rejected, but failed to send email: {str(e)}")

        with transaction.atomic():
            for rejected_user in users_to_delete:
                rejected_user.delete()

        if child_count:
            messages.success(
                request,
                f"Membership request rejected. The parent account and {child_count} linked child request{'s' if child_count != 1 else ''} were also removed.",
            )
        else:
            messages.success(request, "Membership request rejected and account deleted.")
        return redirect("team_management:roster")

    messages.error(request, "Invalid review action.")
    return redirect("team_management:roster")
