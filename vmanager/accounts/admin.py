from django.contrib import admin

from .models import AccountProfile, EmailVerificationCode, NotificationPreferences


@admin.register(AccountProfile)
class AccountProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "club_name", "position", "jersey_number", "created_at")
    list_filter = ("role",)
    search_fields = (
        "user__first_name",
        "user__last_name",
        "user__email",
        "club_name",
        "child_name",
        "position",
        "phone_number",
    )


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "expires_at", "used_at", "created_at")
    list_filter = ("purpose", "used_at")
    search_fields = ("user__email",)


@admin.register(NotificationPreferences)
class NotificationPreferencesAdmin(admin.ModelAdmin):
    list_display = ("user", "email_enabled", "push_enabled", "quiet_hours_enabled", "updated_at")
    list_filter = ("email_enabled", "push_enabled", "quiet_hours_enabled")
    search_fields = ("user__email", "user__first_name", "user__last_name")
