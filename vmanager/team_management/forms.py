from django import forms
from django.contrib.auth import get_user_model

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

    def __init__(self, *args, team=None, membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.membership = membership
        self.resolved_user = membership.user if membership else None

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
            child_first_name = ""
            child_last_name = ""
            if profile.role == AccountProfile.ROLE_PARENT and profile.child_name:
                child_parts = profile.child_name.split(maxsplit=1)
                child_first_name = child_parts[0]
                child_last_name = child_parts[1] if len(child_parts) > 1 else ""
            self.initial.update(
                {
                    "first_name": child_first_name if profile.role == AccountProfile.ROLE_PARENT else membership.user.first_name,
                    "last_name": child_last_name if profile.role == AccountProfile.ROLE_PARENT else membership.user.last_name,
                    "email": membership.user.email,
                    "phone_number": profile.phone_number,
                    "date_of_birth": profile.date_of_birth,
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

        if member_category == self.CATEGORY_PLAYER and not position:
            self.add_error("position", "Position is required for players.")
        if member_category == self.CATEGORY_PLAYER and not member_title:
            self.add_error("member_title", "Member Role is required for players.")

        if member_category == self.CATEGORY_STAFF:
            cleaned_data["jersey_number"] = None
            cleaned_data["position"] = ""
            cleaned_data["member_title"] = profile.get_role_display()
        else:
            cleaned_data["position"] = position
            cleaned_data["member_title"] = member_title

        self.resolved_user = user
        cleaned_data["phone_number"] = (cleaned_data.get("phone_number") or "").strip()
        return cleaned_data

    def save(self, *, team, added_by):
        user = self.resolved_user or self.membership.user
        profile = user.profile
        is_parent_player = profile.role == AccountProfile.ROLE_PARENT and self.cleaned_data["member_category"] == self.CATEGORY_PLAYER
        child_name = f'{self.cleaned_data["first_name"].strip()} {self.cleaned_data["last_name"].strip()}'.strip()

        if is_parent_player:
            profile.child_name = child_name
        else:
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
        membership.is_active = self.cleaned_data.get("is_active", True)
        if membership.added_by_id is None:
            membership.added_by = added_by
        membership.save()
        return membership
