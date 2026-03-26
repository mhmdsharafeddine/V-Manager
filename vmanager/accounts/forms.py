from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import AccountProfile

User = get_user_model()


class RegistrationForm(forms.Form):
    CLUB_INFO_ROLES = {
        AccountProfile.ROLE_MANAGER,
    }
    CHILD_NAME_ROLE = AccountProfile.ROLE_PARENT

    first_name = forms.CharField(max_length=150, label="First Name")
    last_name = forms.CharField(max_length=150, label="Last Name")
    email = forms.EmailField(max_length=254, label="Email Address")
    password = forms.CharField(widget=forms.PasswordInput, strip=False, label="Create Password")
    role = forms.ChoiceField(choices=AccountProfile.ROLE_CHOICES, label="Your Role")
    club_name = forms.CharField(max_length=150, label="Club/Team Name", required=False)
    child_name = forms.CharField(max_length=150, label="Child Name", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_attrs = {
            "first_name": {"placeholder": "e.g. John"},
            "last_name": {"placeholder": "e.g. Doe"},
            "email": {"placeholder": "you@example.com", "autocomplete": "email"},
            "password": {"placeholder": "At least 8 characters", "autocomplete": "new-password"},
            "role": {},
            "club_name": {"placeholder": "e.g. Lions Volleyball Club"},
            "child_name": {"placeholder": "e.g. Adam Haddad"},
        }
        for name, attrs in field_attrs.items():
            css_class = "auth-input auth-select" if name in {"role"} else "auth-input"
            self.fields[name].widget.attrs.update({"class": css_class, **attrs})

        self.fields["role"].choices = [("", "Select Your Role"), *AccountProfile.ROLE_CHOICES]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        club_name = (cleaned_data.get("club_name") or "").strip()
        child_name = (cleaned_data.get("child_name") or "").strip()

        if role in self.CLUB_INFO_ROLES:
            if not club_name:
                self.add_error("club_name", "Club/Team Name is required for this role.")
        else:
            cleaned_data["club_name"] = ""

        if role == self.CHILD_NAME_ROLE:
            if not child_name:
                self.add_error("child_name", "Child Name is required for parents.")
        else:
            cleaned_data["child_name"] = ""

        return cleaned_data

    def save(self):
        data = self.cleaned_data
        email = data["email"]
        user = User.objects.create_user(
            username=email,
            email=email,
            password=data["password"],
            first_name=data["first_name"].strip(),
            last_name=data["last_name"].strip(),
        )
        AccountProfile.objects.create(
            user=user,
            role=data["role"],
            club_name=data["club_name"].strip(),
            child_name=data["child_name"].strip(),
        )
        return user


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
