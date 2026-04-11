from django.contrib import admin

from .models import (
	Announcement,
	AnnouncementMessage,
	AnnouncementMessageRead,
	AnnouncementRecipient,
	UserPresence,
	UserPresenceConnection,
)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
	list_display = ("title", "team", "audience", "priority", "created_by", "created_at")
	list_filter = ("team", "audience", "priority", "pin_to_top", "created_at")
	search_fields = ("title", "body", "created_by__email")


@admin.register(AnnouncementRecipient)
class AnnouncementRecipientAdmin(admin.ModelAdmin):
	list_display = ("announcement", "user", "delivered_at", "read_at")
	list_filter = ("read_at",)
	search_fields = ("announcement__title", "user__email")


@admin.register(AnnouncementMessage)
class AnnouncementMessageAdmin(admin.ModelAdmin):
	list_display = ("announcement", "author", "created_at")
	search_fields = ("announcement__title", "author__email", "body")


@admin.register(AnnouncementMessageRead)
class AnnouncementMessageReadAdmin(admin.ModelAdmin):
	list_display = ("message", "user", "read_at")
	search_fields = ("message__announcement__title", "user__email")


@admin.register(UserPresence)
class UserPresenceAdmin(admin.ModelAdmin):
	list_display = ("user", "last_seen")
	search_fields = ("user__email", "user__first_name", "user__last_name")


@admin.register(UserPresenceConnection)
class UserPresenceConnectionAdmin(admin.ModelAdmin):
	list_display = ("user", "team", "connected_at", "last_heartbeat_at", "disconnected_at")
	list_filter = ("team", "disconnected_at")
	search_fields = ("user__email", "channel_name")
