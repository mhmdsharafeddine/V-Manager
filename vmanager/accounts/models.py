import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class AccountProfile(models.Model):
    ROLE_COACH = "coach"
    ROLE_MANAGER = "manager"
    ROLE_STAFF = "staff"
    ROLE_PLAYER = "player"
    ROLE_PARENT = "parent"

    ROLE_CHOICES = [
        (ROLE_COACH, "Coach"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_STAFF, "Staff"),
        (ROLE_PLAYER, "Player"),
        (ROLE_PARENT, "Parent"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    club_name = models.CharField(max_length=150, blank=True)
    child_name = models.CharField(max_length=150, blank=True)
    linked_player = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_parent",
    )
    phone_number = models.CharField(max_length=40, blank=True)
    date_of_birth = models.DateField(blank=True, null=True)
    jersey_number = models.PositiveIntegerField(blank=True, null=True)
    position = models.CharField(max_length=120, blank=True)
    profile_photo = models.FileField(upload_to="profile_photos/", blank=True)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "user__email"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} ({self.get_role_display()})"

    @property
    def initials(self):
        first = (self.user.first_name or "")[:1]
        last = (self.user.last_name or "")[:1]
        return (first + last).upper() or (self.user.email[:2].upper())

    def linked_children_profiles(self):
        children = []
        child_links = (
            self.child_links.select_related("child_profile__user").all()
            if self.pk
            else []
        )
        for link in child_links:
            if link.child_profile_id and all(child.pk != link.child_profile_id for child in children):
                children.append(link.child_profile)
        if self.linked_player_id and all(child.pk != self.linked_player_id for child in children):
            children.insert(0, self.linked_player)
        return children

    def primary_linked_child(self):
        children = self.linked_children_profiles()
        return children[0] if children else None


class ParentChildLink(models.Model):
    parent_profile = models.ForeignKey(
        AccountProfile,
        on_delete=models.CASCADE,
        related_name="child_links",
    )
    child_profile = models.OneToOneField(
        AccountProfile,
        on_delete=models.CASCADE,
        related_name="parent_link",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent_profile", "child_profile"],
                name="uniq_parent_child_link",
            ),
        ]

    def __str__(self):
        return f"{self.parent_profile} -> {self.child_profile}"


class EmailVerificationCode(models.Model):
    PURPOSE_LOGIN_2FA = "login_2fa"
    PURPOSE_PASSWORD_RESET = "password_reset"

    PURPOSE_CHOICES = [
        (PURPOSE_LOGIN_2FA, "Login 2FA"),
        (PURPOSE_PASSWORD_RESET, "Password Reset"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_codes",
    )
    purpose = models.CharField(max_length=30, choices=PURPOSE_CHOICES)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def create_code(cls, user, purpose: str, *, lifetime_minutes: int = 10):
        code = f"{secrets.randbelow(900000) + 100000}"
        expires_at = timezone.now() + timedelta(minutes=lifetime_minutes)
        record = cls.objects.create(
            user=user,
            purpose=purpose,
            code_hash=cls.hash_code(code),
            expires_at=expires_at,
        )
        return record, code

    def is_valid(self, code: str) -> bool:
        return (
            self.used_at is None
            and self.expires_at >= timezone.now()
            and self.code_hash == self.hash_code(code)
        )


class NotificationPreferences(models.Model):
    """Per-user notification preferences. Created on first save with sane defaults."""

    DIGEST_INSTANT = "instant"
    DIGEST_DAILY = "daily"
    DIGEST_CHOICES = [
        (DIGEST_INSTANT, "Instant"),
        (DIGEST_DAILY, "Daily digest"),
    ]

    PRIORITY_ALL = "all"
    PRIORITY_IMPORTANT_UP = "important_up"
    PRIORITY_URGENT_ONLY = "urgent_only"
    PRIORITY_CHOICES = [
        (PRIORITY_ALL, "All priorities"),
        (PRIORITY_IMPORTANT_UP, "Important & Urgent only"),
        (PRIORITY_URGENT_ONLY, "Urgent only"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )

    # ── Global toggles ────────────────────────────────────────────────────────
    email_enabled = models.BooleanField(default=True)
    push_enabled = models.BooleanField(default=True)

    # ── Per-channel toggles ───────────────────────────────────────────────────
    announcements_enabled = models.BooleanField(default=True)
    chat_enabled = models.BooleanField(default=True)
    scheduling_enabled = models.BooleanField(default=True)

    # ── Digest / frequency ────────────────────────────────────────────────────
    announcement_digest = models.CharField(
        max_length=10, choices=DIGEST_CHOICES, default=DIGEST_INSTANT
    )
    chat_digest = models.CharField(
        max_length=10, choices=DIGEST_CHOICES, default=DIGEST_INSTANT
    )

    # ── Priority filter ───────────────────────────────────────────────────────
    announcement_min_priority = models.CharField(
        max_length=15, choices=PRIORITY_CHOICES, default=PRIORITY_ALL
    )

    # ── Quiet hours ───────────────────────────────────────────────────────────
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_start = models.TimeField(default="22:00")
    quiet_end = models.TimeField(default="08:00")
    quiet_skip_entirely = models.BooleanField(
        default=False,
        help_text="If True, notifications during quiet hours are dropped. "
                  "If False, they are held and sent after quiet hours end.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "notification preferences"
        verbose_name_plural = "notification preferences"

    def __str__(self):
        return f"NotificationPreferences(user={self.user_id})"

    @classmethod
    def for_user(cls, user):
        """Get-or-create the preferences record for a user."""
        prefs, _ = cls.objects.get_or_create(user=user)
        return prefs
