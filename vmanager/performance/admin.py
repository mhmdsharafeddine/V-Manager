from django.contrib import admin

from .models import TeamPerformanceRecord


@admin.register(TeamPerformanceRecord)
class TeamPerformanceRecordAdmin(admin.ModelAdmin):
	list_display = (
		"event",
		"member",
		"result",
		"points_scored",
		"points_conceded",
		"unforced_errors",
		"target_achieved",
		"updated_at",
	)
	list_filter = ("result", "target_achieved", "team")
	search_fields = ("event__title", "member__user__first_name", "member__user__last_name", "member__user__email")
