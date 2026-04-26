from django import forms
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from django.utils.crypto import get_random_string

from accounts.models import AccountProfile

from .models import TeamMembership

User = get_user_model()


class TeamMemberForm(forms.Form):
    CATEGORY_PLAYER = "player"
    CATEGORY_STAFF = "staff"
    POSITION_CHOICES = [
        ("", "Select Position"),
        ("Setter", "Setter"),
        ("Libero", "Libero"),
        ("Outside Hitter", "Outside Hitter"),
        ("Opposite Hitter", "Opposite Hitter"),
        ("Middle Blocker", "Middle Blocker"),
    ]
    MEMBER_ROLE_CHOICES = [
        ("", "Select Member Role"),
        ("Player", "Player"),
        ("Captain", "Captain"),
        ("Parent", "Parent"),
        ("Coach", "Coach"),
        ("Staff", "Staff"),
    ]

    CATEGORY_CHOICES = [
        (CATEGORY_PLAYER, "Player"),
        (CATEGORY_STAFF, "Staff/Coach"),
    ]

    first_name = forms.CharField(max_length=150, label="First Name")
    last_name = forms.CharField(max_length=150, label="Last Name")
    email = forms.EmailField(max_length=254, label="Email Address")
    phone_number = forms.CharField(max_length=40, label="Phone Number", required=False)
    date_of_birth = forms.DateField(
        label="Date Of Birth",
        required=False,
        widget=forms.DateInput(
            attrs={"type": "date"},
            format="%Y-%m-%d",
        ),
    )
    linked_children = forms.CharField(
        label="Linked Children",
        required=False,
        disabled=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    jersey_number = forms.IntegerField(label="Jersey Number", required=False, min_value=0)
    position = forms.ChoiceField(choices=POSITION_CHOICES, label="Position", required=False)
    member_title = forms.ChoiceField(choices=MEMBER_ROLE_CHOICES, label="Member Role", required=False)
    member_category = forms.ChoiceField(
        choices=CATEGORY_CHOICES,
        widget=forms.HiddenInput(),
        initial=CATEGORY_PLAYER,
    )
    profile_photo = forms.FileField(label="Profile Photo", required=False)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, team=None, membership=None, actor_role="", **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.membership = membership
        self.actor_role = actor_role
        self.resolved_user = membership.user if membership else None

        input_fields = {
            "first_name": {"placeholder": "e.g. John"},
            "last_name": {"placeholder": "e.g. Doe"},
            "email": {"placeholder": "you@example.com", "autocomplete": "email"},
            "phone_number": {"placeholder": "e.g. +961 03 046 997", "autocomplete": "tel"},
            "date_of_birth": {"type": "date"},
            "linked_children": {"placeholder": "Linked child names", "readonly": "readonly"},
            "jersey_number": {"placeholder": "e.g. 10", "inputmode": "numeric"},
        }

        for field_name, attrs in input_fields.items():
            self.fields[field_name].widget.attrs.update({"class": "team-input", **attrs})

        for field_name in {"position", "member_title"}:
            self.fields[field_name].widget.attrs.update({"class": "team-input team-select"})

        self.fields["profile_photo"].widget.attrs.update(
            {
                "class": "team-file-input",
                "accept": "image/*",
            }
        )
        self.fields["is_active"].widget.attrs.update({"class": "team-checkbox"})

        if membership:
            profile = membership.user.profile
            linked_children_summary = ""
            if profile.role == AccountProfile.ROLE_PARENT:
                linked_children = profile.linked_children_profiles()
                if linked_children:
                    linked_children_summary = "\n".join(
                        child.user.get_full_name().strip() or child.user.email
                        for child in linked_children
                    )
                elif profile.child_name:
                    linked_children_summary = profile.child_name
            self.initial.update(
                {
                    "first_name": membership.user.first_name,
                    "last_name": membership.user.last_name,
                    "email": membership.user.email,
                    "phone_number": profile.phone_number,
                    "date_of_birth": profile.date_of_birth,
                    "linked_children": linked_children_summary,
                    "jersey_number": profile.jersey_number,
                    "position": profile.position,
                    "member_title": membership.member_title,
                    "member_category": (
                        self.CATEGORY_PLAYER
                        if profile.role in {AccountProfile.ROLE_PLAYER, AccountProfile.ROLE_PARENT}
                        else self.CATEGORY_STAFF
                    ),
                    "is_active": membership.is_active,
                }
            )
            self.fields["email"].disabled = True

    def clean_email(self):
        if self.membership:
            return self.membership.user.email
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned_data = super().clean()
        if self.team is None:
            raise forms.ValidationError("A team is required for this action.")

        email = cleaned_data.get("email")
        member_category = cleaned_data.get("member_category") or self.CATEGORY_PLAYER
        position = (cleaned_data.get("position") or "").strip()
        member_title = (cleaned_data.get("member_title") or "").strip()

        if not email:
            return cleaned_data

        if self.membership:
            user = self.membership.user
        else:
            user = User.objects.filter(email__iexact=email).select_related("profile").first()

        if user is None:
            self.add_error("email", "This user must create an account before you can add them to the roster.")
            return cleaned_data

        profile = getattr(user, "profile", None)
        if profile is None:
            self.add_error("email", "This account is missing a profile and cannot be added yet.")
            return cleaned_data

        if self.membership is None and profile.role == AccountProfile.ROLE_MANAGER:
            self.add_error("email", "Managers must create their own account through manager registration.")
            return cleaned_data

        if (
            self.membership is None
            and self.actor_role == AccountProfile.ROLE_STAFF
            and profile.role == AccountProfile.ROLE_STAFF
        ):
            self.add_error("email", "Only managers can add staff members to the roster.")
            return cleaned_data

        if member_category == self.CATEGORY_PLAYER and profile.role not in {
            AccountProfile.ROLE_PLAYER,
            AccountProfile.ROLE_PARENT,
        }:
            self.add_error("email", "Only player or parent accounts can be added in the Player tab.")
        elif member_category == self.CATEGORY_STAFF and profile.role not in {
            AccountProfile.ROLE_COACH,
            AccountProfile.ROLE_STAFF,
            AccountProfile.ROLE_MANAGER,
        }:
            self.add_error("email", "Only coach, staff, or manager accounts can be added in the Staff/Coach tab.")

        existing_membership = getattr(user, "team_membership", None)
        if existing_membership and (self.membership is None or existing_membership.pk != self.membership.pk):
            if existing_membership.team_id == self.team.id:
                self.add_error("email", "This member is already on your roster.")
            else:
                self.add_error("email", "This member is already assigned to another team.")

        if member_category == self.CATEGORY_PLAYER and not member_title:
            self.add_error("member_title", "Member Role is required for players.")

        is_parent_member = member_category == self.CATEGORY_PLAYER and member_title == "Parent"
        if member_category == self.CATEGORY_PLAYER and not is_parent_member and not position:
            self.add_error("position", "Position is required for players.")

        if member_category == self.CATEGORY_STAFF:
            cleaned_data["jersey_number"] = None
            cleaned_data["position"] = ""
            cleaned_data["member_title"] = profile.get_role_display()
        elif is_parent_member:
            cleaned_data["jersey_number"] = None
            cleaned_data["position"] = ""
            cleaned_data["member_title"] = "Parent"
        else:
            cleaned_data["position"] = position
            cleaned_data["member_title"] = member_title

        self.resolved_user = user
        cleaned_data["phone_number"] = (cleaned_data.get("phone_number") or "").strip()
        return cleaned_data

    def save(self, *, team, added_by):
        user = self.resolved_user or self.membership.user
        profile = user.profile
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.save(update_fields=["first_name", "last_name"])

        profile.phone_number = self.cleaned_data["phone_number"]
        profile.date_of_birth = self.cleaned_data["date_of_birth"]
        profile.jersey_number = self.cleaned_data["jersey_number"]
        profile.position = self.cleaned_data["position"]
        if self.cleaned_data.get("profile_photo"):
            profile.profile_photo = self.cleaned_data["profile_photo"]
        profile.save()

        membership = self.membership or TeamMembership(user=user, team=team, added_by=added_by)
        membership.team = team
        membership.member_title = self.cleaned_data["member_title"]
        # New roster members should always start active; the active toggle is only used when editing.
        membership.is_active = self.cleaned_data.get("is_active", True) if self.membership else True
        if membership.added_by_id is None:
            membership.added_by = added_by
        membership.save()
        return membership


class RosterAccountInviteForm(forms.Form):
    INVITE_ROLE_PLAYER = "player"
    INVITE_ROLE_CAPTAIN = "captain"
    INVITE_ROLE_PARENT = "parent"
    INVITE_ROLE_COACH = "coach"
    INVITE_ROLE_STAFF = "staff"

    PLAYER_STYLE_ROLES = {INVITE_ROLE_PLAYER, INVITE_ROLE_CAPTAIN}

    POSITION_CHOICES = TeamMemberForm.POSITION_CHOICES

    ROLE_CHOICES = [
        (INVITE_ROLE_PLAYER, "Player"),
        (INVITE_ROLE_CAPTAIN, "Captain"),
        (INVITE_ROLE_PARENT, "Parent"),
        (INVITE_ROLE_COACH, "Coach"),
        (INVITE_ROLE_STAFF, "Staff"),
    ]

    first_name = forms.CharField(max_length=150, label="First Name")
    last_name = forms.CharField(max_length=150, label="Last Name")
    email = forms.EmailField(max_length=254, label="Email Address")
    phone_number = forms.CharField(max_length=40, label="Phone Number", required=False)
    date_of_birth = forms.DateField(
        label="Date Of Birth",
        required=False,
        widget=forms.DateInput(
            attrs={"type": "date"},
            format="%Y-%m-%d",
        ),
    )
    invite_role = forms.ChoiceField(choices=ROLE_CHOICES, label="Role To Create")
    jersey_number = forms.IntegerField(label="Jersey Number", required=False, min_value=0)
    position = forms.ChoiceField(choices=POSITION_CHOICES, label="Position", required=False)
    profile_photo = forms.FileField(label="Profile Photo", required=False)

    def __init__(self, *args, actor=None, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.team = team

        actor_role = getattr(getattr(actor, "profile", None), "role", "")
        if actor_role == AccountProfile.ROLE_STAFF:
            self.fields["invite_role"].choices = [
                choice for choice in self.ROLE_CHOICES if choice[0] != self.INVITE_ROLE_STAFF
            ]

        input_fields = {
            "first_name": {"placeholder": "e.g. John"},
            "last_name": {"placeholder": "e.g. Doe"},
            "email": {"placeholder": "you@example.com", "autocomplete": "email"},
            "phone_number": {"placeholder": "e.g. +961 03 046 997", "autocomplete": "tel"},
            "date_of_birth": {"type": "date"},
            "jersey_number": {"placeholder": "e.g. 10", "inputmode": "numeric"},
        }
        for field_name, attrs in input_fields.items():
            self.fields[field_name].widget.attrs.update({"class": "team-input", **attrs})

        self.fields["invite_role"].widget.attrs.update({"class": "team-input team-select"})
        self.fields["position"].widget.attrs.update({"class": "team-input team-select"})
        self.fields["profile_photo"].widget.attrs.update(
            {
                "class": "team-file-input",
                "accept": "image/*",
            }
        )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned_data = super().clean()
        invite_role = (cleaned_data.get("invite_role") or "").strip()
        email = cleaned_data.get("email")
        actor_role = getattr(getattr(self.actor, "profile", None), "role", "")

        if self.team is None or self.actor is None:
            raise forms.ValidationError("A team and inviter are required for this action.")

        if actor_role not in {AccountProfile.ROLE_MANAGER, AccountProfile.ROLE_STAFF}:
            raise forms.ValidationError("Only managers and staff members can create roster accounts.")

        if actor_role == AccountProfile.ROLE_STAFF and invite_role == self.INVITE_ROLE_STAFF:
            self.add_error("invite_role", "Only managers can create staff accounts.")

        if email and (
            User.objects.filter(email__iexact=email).exists()
            or User.objects.filter(username__iexact=email).exists()
        ):
            self.add_error("email", "An account with this email already exists.")

        position = (cleaned_data.get("position") or "").strip()
        if invite_role in self.PLAYER_STYLE_ROLES and not position:
            self.add_error("position", "Position is required for players and captains.")

        if invite_role not in self.PLAYER_STYLE_ROLES:
            cleaned_data["jersey_number"] = None
            cleaned_data["position"] = ""
        else:
            cleaned_data["position"] = position

        cleaned_data["phone_number"] = (cleaned_data.get("phone_number") or "").strip()
        return cleaned_data

    def _resolved_profile_role(self):
        invite_role = self.cleaned_data["invite_role"]
        if invite_role == self.INVITE_ROLE_CAPTAIN:
            return AccountProfile.ROLE_PLAYER
        return invite_role

    def _resolved_member_title(self):
        return {
            self.INVITE_ROLE_PLAYER: "Player",
            self.INVITE_ROLE_CAPTAIN: "Captain",
            self.INVITE_ROLE_PARENT: "Parent",
            self.INVITE_ROLE_COACH: "Coach",
            self.INVITE_ROLE_STAFF: "Staff",
        }[self.cleaned_data["invite_role"]]

    def _generate_temporary_password(self):
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%"
        return get_random_string(12, allowed_chars=alphabet)

    def save(self, *, team, added_by):
        temporary_password = self._generate_temporary_password()
        email = self.cleaned_data["email"]
        profile_role = self._resolved_profile_role()

        try:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=temporary_password,
                first_name=self.cleaned_data["first_name"].strip(),
                last_name=self.cleaned_data["last_name"].strip(),
            )
        except IntegrityError as exc:
            raise forms.ValidationError({"email": ["An account with this email already exists."]}) from exc

        profile = AccountProfile.objects.create(
            user=user,
            role=profile_role,
            club_name=team.name,
            child_name="",
            phone_number=self.cleaned_data["phone_number"],
            date_of_birth=self.cleaned_data.get("date_of_birth"),
            jersey_number=self.cleaned_data.get("jersey_number"),
            position=self.cleaned_data.get("position", ""),
            must_change_password=True,
        )
        if self.cleaned_data.get("profile_photo"):
            profile.profile_photo = self.cleaned_data["profile_photo"]
            profile.save(update_fields=["profile_photo"])

        membership = TeamMembership.objects.create(
            user=user,
            team=team,
            member_title=self._resolved_member_title(),
            requested_role=profile_role,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=added_by,
            reviewed_by=added_by,
            reviewed_at=timezone.now(),
        )
        return membership, temporary_password
