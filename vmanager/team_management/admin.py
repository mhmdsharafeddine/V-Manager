from django.contrib import admin

from .models import Team, TeamMembership


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name", "created_by__email", "created_by__first_name", "created_by__last_name")


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "member_title", "is_active", "joined_at")
    list_filter = ("is_active", "team")
    search_fields = ("user__email", "user__first_name", "user__last_name", "team__name", "member_title")
