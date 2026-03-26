from django.contrib import admin

from .models import AccountProfile, EmailVerificationCode


@admin.register(AccountProfile)
class AccountProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "club_name", "child_name", "created_at")
    list_filter = ("role",)
    search_fields = ("user__first_name", "user__last_name", "user__email", "club_name", "child_name")


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "expires_at", "used_at", "created_at")
    list_filter = ("purpose", "used_at")
    search_fields = ("user__email",)
