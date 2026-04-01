from django.contrib import admin

from .models import ScheduledEvent


@admin.register(ScheduledEvent)
class ScheduledEventAdmin(admin.ModelAdmin):
	list_display = ("title", "event_type", "scheduled_at", "attendees_count", "team", "created_by", "status")
	list_filter = ("event_type", "status", "scheduled_at")
	search_fields = ("title", "location", "details", "created_by__email", "created_by__first_name", "created_by__last_name")
