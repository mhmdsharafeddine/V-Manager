from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.forms import BaseFormSet, formset_factory

from .models import AccountProfile, ParentChildLink
from team_management.models import Team, TeamMembership

User = get_user_model()


def _normalize_email(value):
    return (value or "").strip().lower()


def _email_exists(email):
    return User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists()


def _email_exists_for_other_user(email, *, user=None):
    queryset = User.objects.filter(email__iexact=email) | User.objects.filter(username__iexact=email)
    if user is not None and getattr(user, "pk", None):
        queryset = queryset.exclude(pk=user.pk)
    return queryset.exists()


class LinkedPlayerRegistrationForm(forms.Form):
    first_name = forms.CharField(max_length=150, label="Child Name")
    last_name = forms.CharField(max_length=150, label="Child Family Name")
    date_of_birth = forms.DateField(
        label="Child Birthday",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    email = forms.EmailField(max_length=254, label="Child Email Address")
    password = forms.CharField(widget=forms.PasswordInput, strip=False, label="Child Password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_attrs = {
            "first_name": {"placeholder": "e.g. Adam"},
            "last_name": {"placeholder": "e.g. Saab"},
            "date_of_birth": {},
            "email": {"placeholder": "child@example.com", "autocomplete": "email"},
            "password": {"placeholder": "At least 8 characters", "autocomplete": "new-password"},
        }
        for name, attrs in field_attrs.items():
            self.fields[name].widget.attrs.update({"class": "auth-input", **attrs})

    def clean_email(self):
        email = _normalize_email(self.cleaned_data["email"])
        if _email_exists(email):
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password


class BaseLinkedPlayerRegistrationFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return

        seen_emails = set()
        non_deleted_forms = 0

        for form in self.forms:
            cleaned_data = getattr(form, "cleaned_data", None) or {}
            if not cleaned_data or cleaned_data.get("DELETE"):
                continue
            non_deleted_forms += 1
            email = cleaned_data.get("email")
            if not email:
                continue
            if email in seen_emails:
                form.add_error("email", "Each linked child must use a different email address.")
            seen_emails.add(email)

        if non_deleted_forms < 1:
            raise forms.ValidationError("Please add at least one linked child account.")


LinkedPlayerRegistrationFormSet = formset_factory(
    LinkedPlayerRegistrationForm,
    formset=BaseLinkedPlayerRegistrationFormSet,
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class RegistrationForm(forms.Form):
    SIGNUP_MANAGER = "manager"
    SIGNUP_MEMBER = "member"

    TEAM_MEMBER_ROLE_CHOICES = [
        (AccountProfile.ROLE_PLAYER, "Player"),
        (AccountProfile.ROLE_PARENT, "Parent"),
        (AccountProfile.ROLE_COACH, "Coach"),
        (AccountProfile.ROLE_STAFF, "Staff"),
    ]

    signup_type = forms.ChoiceField(
        choices=[
            (SIGNUP_MANAGER, "Team Manager"),
            (SIGNUP_MEMBER, "Team Member"),
        ],
        label="Sign Up As",
    )
    first_name = forms.CharField(max_length=150, label="Name")
    last_name = forms.CharField(max_length=150, label="Family Name")
    date_of_birth = forms.DateField(
        label="Birthday",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    email = forms.EmailField(max_length=254, label="Email Address")
    password = forms.CharField(widget=forms.PasswordInput, strip=False, label="Create Password")
    team_name = forms.CharField(max_length=150, label="Team Name", required=False)
    team = forms.ChoiceField(choices=[("", "Select Team")], label="Choose Team", required=False)
    requested_role = forms.ChoiceField(choices=[("", "Select Team Role")], label="Requested Role", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        team_choices = [("", "Select Team")]
        team_choices.extend((str(team.id), team.name) for team in Team.objects.order_by("name"))
        self.fields["team"].choices = team_choices
        self.fields["requested_role"].choices = [("", "Select Team Role"), *self.TEAM_MEMBER_ROLE_CHOICES]

        field_attrs = {
            "signup_type": {},
            "first_name": {"placeholder": "e.g. Maya"},
            "last_name": {"placeholder": "e.g. Saab"},
            "date_of_birth": {},
            "email": {"placeholder": "you@example.com", "autocomplete": "email"},
            "password": {"placeholder": "At least 8 characters", "autocomplete": "new-password"},
            "team_name": {"placeholder": "e.g. Lions Volleyball Club"},
            "team": {},
            "requested_role": {},
        }
        for name, attrs in field_attrs.items():
            css_class = "auth-input auth-select" if name in {"signup_type", "team", "requested_role"} else "auth-input"
            self.fields[name].widget.attrs.update({"class": css_class, **attrs})

    def clean_email(self):
        email = _normalize_email(self.cleaned_data["email"])
        if _email_exists(email):
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        signup_type = cleaned_data.get("signup_type")
        team_name = (cleaned_data.get("team_name") or "").strip()
        team_id = (cleaned_data.get("team") or "").strip()
        requested_role = cleaned_data.get("requested_role")

        if signup_type == self.SIGNUP_MANAGER:
            if not team_name:
                self.add_error("team_name", "Team Name is required for team managers.")
            elif Team.objects.filter(name__iexact=team_name).exists():
                self.add_error("team_name", "A team with this name already exists.")
            cleaned_data["team"] = ""
            cleaned_data["requested_role"] = ""
        elif signup_type == self.SIGNUP_MEMBER:
            if not team_id:
                self.add_error("team", "Please choose a team.")
            if not requested_role:
                self.add_error("requested_role", "Please choose your role in this team.")
            try:
                selected_team = Team.objects.get(pk=int(team_id))
            except (TypeError, ValueError, Team.DoesNotExist):
                selected_team = None
                self.add_error("team", "Selected team is invalid.")
            cleaned_data["selected_team"] = selected_team
            cleaned_data["team_name"] = ""

        return cleaned_data

    def save(self, *, linked_children=None):
        data = self.cleaned_data
        email = data["email"]
        signup_type = data["signup_type"]
        linked_children = linked_children or []
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=data["password"],
                    first_name=data["first_name"].strip(),
                    last_name=data["last_name"].strip(),
                )

                if signup_type == self.SIGNUP_MANAGER:
                    team_name = data["team_name"].strip()
                    AccountProfile.objects.create(
                        user=user,
                        role=AccountProfile.ROLE_MANAGER,
                        club_name=team_name,
                        child_name="",
                        date_of_birth=data["date_of_birth"],
                    )
                    team = Team.objects.create(name=team_name, created_by=user)
                    TeamMembership.objects.create(
                        user=user,
                        team=team,
                        member_title="Team Manager",
                        requested_role=AccountProfile.ROLE_MANAGER,
                        status=TeamMembership.STATUS_APPROVED,
                        is_active=True,
                        added_by=user,
                        reviewed_by=user,
                    )
                else:
                    selected_team = data.get("selected_team")
                    requested_role = data["requested_role"]
                    profile = AccountProfile.objects.create(
                        user=user,
                        role=requested_role,
                        club_name="",
                        child_name="",
                        date_of_birth=data["date_of_birth"],
                    )
                    TeamMembership.objects.create(
                        user=user,
                        team=selected_team,
                        member_title=f"Pending {dict(AccountProfile.ROLE_CHOICES).get(requested_role, 'Member')}",
                        requested_role=requested_role,
                        status=TeamMembership.STATUS_PENDING,
                        is_active=False,
                    )
                    if requested_role == AccountProfile.ROLE_PARENT:
                        primary_child_profile = None
                        for child_data in linked_children:
                            child_email = child_data["email"]
                            child_user = User.objects.create_user(
                                username=child_email,
                                email=child_email,
                                password=child_data["password"],
                                first_name=child_data["first_name"].strip(),
                                last_name=child_data["last_name"].strip(),
                            )
                            child_profile = AccountProfile.objects.create(
                                user=child_user,
                                role=AccountProfile.ROLE_PLAYER,
                                club_name="",
                                child_name="",
                                date_of_birth=child_data["date_of_birth"],
                            )
                            TeamMembership.objects.create(
                                user=child_user,
                                team=selected_team,
                                member_title="Pending Player",
                                requested_role=AccountProfile.ROLE_PLAYER,
                                status=TeamMembership.STATUS_PENDING,
                                is_active=False,
                            )
                            ParentChildLink.objects.create(
                                parent_profile=profile,
                                child_profile=child_profile,
                            )
                            if primary_child_profile is None:
                                primary_child_profile = child_profile

                        if primary_child_profile is not None:
                            profile.child_name = primary_child_profile.user.get_full_name().strip()
                            profile.linked_player = primary_child_profile
                            profile.save(update_fields=["child_name", "linked_player"])
                return user
        except IntegrityError as exc:
            raise forms.ValidationError("An account with this email already exists.") from exc


class AccountSettingsForm(forms.Form):
    first_name = forms.CharField(max_length=150, label="Name")
    last_name = forms.CharField(max_length=150, label="Family Name")
    email = forms.EmailField(max_length=254, label="Email Address")
    phone_number = forms.CharField(max_length=40, label="Phone Number", required=False)
    date_of_birth = forms.DateField(
        label="Birthday",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    profile_photo = forms.FileField(label="Profile Photo", required=False)
    remove_profile_photo = forms.BooleanField(label="Remove photo", required=False)
    current_password = forms.CharField(
        widget=forms.PasswordInput,
        strip=False,
        required=False,
        label="Current Password",
    )
    new_password1 = forms.CharField(
        widget=forms.PasswordInput,
        strip=False,
        required=False,
        label="New Password",
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput,
        strip=False,
        required=False,
        label="Confirm New Password",
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        profile = getattr(user, "profile", None)
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email
        self.fields["phone_number"].initial = getattr(profile, "phone_number", "")
        self.fields["date_of_birth"].initial = getattr(profile, "date_of_birth", None)

        field_attrs = {
            "first_name": {"placeholder": "e.g. Maya"},
            "last_name": {"placeholder": "e.g. Saab"},
            "email": {"placeholder": "you@example.com", "autocomplete": "email"},
            "phone_number": {"placeholder": "e.g. +961 03 046 997", "autocomplete": "tel"},
            "date_of_birth": {},
            "profile_photo": {"class": "settings-file-input", "accept": "image/*"},
            "current_password": {"placeholder": "Enter current password", "autocomplete": "current-password"},
            "new_password1": {"placeholder": "At least 8 characters", "autocomplete": "new-password"},
            "new_password2": {"placeholder": "Re-enter new password", "autocomplete": "new-password"},
        }
        for name, attrs in field_attrs.items():
            if name == "profile_photo":
                self.fields[name].widget.attrs.update(attrs)
            else:
                self.fields[name].widget.attrs.update({"class": "auth-input", **attrs})

        self.fields["remove_profile_photo"].widget.attrs.update({"class": "settings-checkbox-input"})

    def clean_email(self):
        email = _normalize_email(self.cleaned_data["email"])
        if _email_exists_for_other_user(email, user=self.user):
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        current_password = cleaned_data.get("current_password") or ""
        new_password1 = cleaned_data.get("new_password1") or ""
        new_password2 = cleaned_data.get("new_password2") or ""

        wants_password_change = any([current_password, new_password1, new_password2])
        if not wants_password_change:
            return cleaned_data

        if not current_password:
            self.add_error("current_password", "Please enter your current password.")
        elif not self.user.check_password(current_password):
            self.add_error("current_password", "Current password is incorrect.")

        if not new_password1:
            self.add_error("new_password1", "Please enter a new password.")
        if not new_password2:
            self.add_error("new_password2", "Please confirm your new password.")
        if new_password1 and new_password2 and new_password1 != new_password2:
            self.add_error("new_password2", "Passwords do not match.")

        if new_password1 and not self.errors.get("new_password1") and not self.errors.get("new_password2"):
            try:
                validate_password(new_password1, self.user)
            except forms.ValidationError as exc:
                self.add_error("new_password1", exc)

        return cleaned_data

    def save(self):
        profile = getattr(self.user, "profile", None)

        self.user.first_name = self.cleaned_data["first_name"].strip()
        self.user.last_name = self.cleaned_data["last_name"].strip()
        self.user.email = self.cleaned_data["email"]
        self.user.username = self.cleaned_data["email"]

        update_password = bool(self.cleaned_data.get("new_password1"))
        if update_password:
            self.user.set_password(self.cleaned_data["new_password1"])
            self.user.save()
        else:
            self.user.save(update_fields=["first_name", "last_name", "email", "username"])

        if profile is not None:
            profile.phone_number = (self.cleaned_data.get("phone_number") or "").strip()
            profile.date_of_birth = self.cleaned_data.get("date_of_birth")

            if self.cleaned_data.get("remove_profile_photo") and profile.profile_photo:
                profile.profile_photo.delete(save=False)
                profile.profile_photo = ""

            if self.cleaned_data.get("profile_photo"):
                profile.profile_photo = self.cleaned_data["profile_photo"]

            profile.save()

        return self.user


class LoginForm(forms.Form):
    email = forms.EmailField(max_length=254, label="Email Address")
    password = forms.CharField(widget=forms.PasswordInput, strip=False, label="Password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {
                "class": "auth-input",
                "placeholder": "you@example.com",
                "autocomplete": "email",
            }
        )
        self.fields["password"].widget.attrs.update(
            {
                "class": "auth-input",
                "placeholder": "Enter your password",
                "autocomplete": "current-password",
            }
        )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class TwoFactorCodeForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6, label="Verification Code")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs.update(
            {
                "class": "auth-input auth-code-input",
                "placeholder": "Enter 6-digit code",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "maxlength": "6",
            }
        )

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise forms.ValidationError("Enter the 6-digit code sent to your email.")
        return code


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(max_length=254, label="Email Address")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {
                "class": "auth-input",
                "placeholder": "you@example.com",
                "autocomplete": "email",
            }
        )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class PasswordResetCodeForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6, label="Reset Code")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs.update(
            {
                "class": "auth-input auth-code-input",
                "placeholder": "Enter 6-digit code",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "maxlength": "6",
            }
        )

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise forms.ValidationError("Enter the 6-digit code sent to your email.")
        return code


class PasswordResetConfirmForm(forms.Form):
    new_password1 = forms.CharField(
        widget=forms.PasswordInput,
        strip=False,
        label="New Password",
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput,
        strip=False,
        label="Confirm New Password",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].widget.attrs.update(
            {
                "class": "auth-input",
                "placeholder": "Enter your new password",
                "autocomplete": "new-password",
            }
        )
        self.fields["new_password2"].widget.attrs.update(
            {
                "class": "auth-input",
                "placeholder": "Confirm your new password",
                "autocomplete": "new-password",
            }
        )

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", "Passwords do not match.")
        if password1:
            validate_password(password1)
        return cleaned_data
