from django.conf import settings
from django.db import models
from django.db.models import Q

from team_management.models import Team


class ScheduledEvent(models.Model):
	TYPE_PRACTICE = "practice"
	TYPE_MATCH = "match"
	TYPE_TOURNAMENT = "tournament"
	TYPE_TRAINING = "training"
	TYPE_OTHER = "other"

	EVENT_TYPE_CHOICES = [
		(TYPE_PRACTICE, "Practice"),
		(TYPE_MATCH, "Match"),
		(TYPE_TOURNAMENT, "Tournament"),
		(TYPE_TRAINING, "Strength Training"),
		(TYPE_OTHER, "Other"),
	]

	STATUS_SCHEDULED = "scheduled"
	STATUS_CANCELLED = "cancelled"
	STATUS_CHOICES = [
		(STATUS_SCHEDULED, "Scheduled"),
		(STATUS_CANCELLED, "Cancelled"),
	]

	AUDIENCE_ALL = "all"
	AUDIENCE_PLAYERS = "players"
	AUDIENCE_COACHES = "coaches"
	AUDIENCE_STAFF = "staff"
	AUDIENCE_PARENTS = "parents"
	AUDIENCE_MANAGERS = "managers"
	AUDIENCE_PRIVATE = "private"
	AUDIENCE_CHOICES = [
		(AUDIENCE_ALL, "Entire Team"),
		(AUDIENCE_PLAYERS, "Players"),
		(AUDIENCE_COACHES, "Coaches"),
		(AUDIENCE_STAFF, "Staff"),
		(AUDIENCE_PARENTS, "Parents"),
		(AUDIENCE_MANAGERS, "Managers"),
		(AUDIENCE_PRIVATE, "Only Me"),
	]

	team = models.ForeignKey(
		Team,
		on_delete=models.CASCADE,
		related_name="events",
		null=True,
		blank=True,
	)
	created_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="scheduled_events",
	)
	title = models.CharField(max_length=160)
	event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default=TYPE_OTHER)
	scheduled_at = models.DateTimeField()
	location = models.CharField(max_length=180, blank=True)
	details = models.TextField(blank=True)
	attendees_count = models.PositiveIntegerField(default=0)
	duration_minutes = models.PositiveIntegerField(default=90)
	audience = models.CharField(max_length=120, default=AUDIENCE_ALL)
	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["scheduled_at", "id"]
		constraints = [
			models.UniqueConstraint(
				fields=["team", "scheduled_at"],
				condition=Q(team__isnull=False),
				name="uniq_team_scheduled_slot",
			),
			models.UniqueConstraint(
				fields=["created_by", "scheduled_at"],
				condition=Q(team__isnull=True),
				name="uniq_personal_scheduled_slot",
			),
		]

	def __str__(self):
		return f"{self.title} ({self.scheduled_at:%Y-%m-%d %H:%M})"


class EventNotificationRead(models.Model):
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="event_notification_reads",
	)
	event = models.ForeignKey(
		ScheduledEvent,
		on_delete=models.CASCADE,
		related_name="notification_reads",
	)
	is_deleted = models.BooleanField(default=False)
	read_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(fields=["user", "event"], name="uniq_notification_read_per_user_event"),
		]

	def __str__(self):
		return f"{self.user_id}:{self.event_id}"


class EventAttendance(models.Model):
	STATUS_ATTENDING = "attending"
	STATUS_MAYBE = "maybe"
	STATUS_NOT_ATTENDING = "not_attending"
	STATUS_CHOICES = [
		(STATUS_ATTENDING, "Attending"),
		(STATUS_MAYBE, "Maybe"),
		(STATUS_NOT_ATTENDING, "Not Attending"),
	]

	REASON_ABSENT = "absent"
	REASON_INJURED = "injured"
	REASON_OTHER = "other"
	NOT_ATTENDING_REASON_CHOICES = [
		(REASON_INJURED, "Injured"),
		(REASON_OTHER, "Other"),
	]

	event = models.ForeignKey(
		ScheduledEvent,
		on_delete=models.CASCADE,
		related_name="attendance_records",
	)
	player = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="event_attendance_records",
	)
	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ATTENDING)
	not_attending_reason = models.CharField(max_length=30, choices=NOT_ATTENDING_REASON_CHOICES, blank=True)
	not_attending_reason_note = models.CharField(max_length=255, blank=True)
	responded_at = models.DateTimeField(auto_now=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-responded_at", "-id"]
		constraints = [
			models.UniqueConstraint(fields=["event", "player"], name="uniq_event_attendance_player"),
		]

	def __str__(self):
		return f"event={self.event_id} player={self.player_id} status={self.status}"
