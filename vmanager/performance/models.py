from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.models import AccountProfile
from scheduling.models import ScheduledEvent
from team_management.models import Team, TeamMembership


class TeamPerformanceRecord(models.Model):
	RESULT_WIN = "win"
	RESULT_LOSS = "loss"
	RESULT_DRAW = "draw"
	RESULT_CHOICES = [
		(RESULT_WIN, "Win"),
		(RESULT_LOSS, "Loss"),
		(RESULT_DRAW, "Draw"),
	]

	PARTICIPATION_PRESENT = "present"
	PARTICIPATION_ABSENT = "absent"
	PARTICIPATION_DID_NOT_ATTEND = "did_not_attend"
	PARTICIPATION_INJURED = "injured"
	PARTICIPATION_EXCUSED = "excused"
	PARTICIPATION_CHOICES = [
		(PARTICIPATION_PRESENT, "Present"),
		(PARTICIPATION_ABSENT, "Absent"),
		(PARTICIPATION_DID_NOT_ATTEND, "Didn't Attend"),
		(PARTICIPATION_INJURED, "Injured"),
		(PARTICIPATION_EXCUSED, "Excused"),
	]

	INJURY_MINOR_ISSUE = "minor_issue"
	INJURY_RECOVERING = "recovering"
	INJURY_RECENTLY_INJURED = "recently_injured"
	INJURY_STATUS_CHOICES = [
		(INJURY_MINOR_ISSUE, "Minor Issue"),
		(INJURY_RECOVERING, "Recovering"),
		(INJURY_RECENTLY_INJURED, "Recently Injured"),
	]

	team = models.ForeignKey(
		Team,
		on_delete=models.CASCADE,
		related_name="performance_records",
	)
	event = models.ForeignKey(
		ScheduledEvent,
		on_delete=models.CASCADE,
		related_name="performance_records",
	)
	member = models.ForeignKey(
		TeamMembership,
		on_delete=models.CASCADE,
		related_name="performance_records",
	)
	recorded_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="recorded_performance_records",
	)
	result = models.CharField(max_length=10, choices=RESULT_CHOICES, default=RESULT_WIN)
	participation_status = models.CharField(
		max_length=20,
		choices=PARTICIPATION_CHOICES,
		default=PARTICIPATION_PRESENT,
	)
	injury_status = models.CharField(
		max_length=20,
		choices=INJURY_STATUS_CHOICES,
		blank=True,
	)
	points_scored = models.PositiveIntegerField(default=0)
	points_conceded = models.PositiveIntegerField(default=0)
	target_score = models.PositiveIntegerField(null=True, blank=True)
	target_achieved = models.BooleanField(default=False)
	kills = models.PositiveIntegerField(default=0)
	aces = models.PositiveIntegerField(default=0)
	blocks = models.PositiveIntegerField(default=0)
	assists = models.PositiveIntegerField(default=0)
	digs = models.PositiveIntegerField(default=0)
	unforced_errors = models.PositiveIntegerField(default=0)
	notes = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-event__scheduled_at", "member__user__first_name", "member__user__last_name"]
		constraints = [
			models.UniqueConstraint(
				fields=["event", "member"],
				name="uniq_performance_record_per_event_member",
			)
		]

	def clean(self):
		if self.event_id and self.event.team_id is None:
			raise ValidationError({"event": "Performance data can only be recorded for team events."})

		if self.event_id and self.team_id and self.event.team_id != self.team_id:
			raise ValidationError({"event": "Selected event does not belong to the selected team."})

		if self.member_id and self.team_id and self.member.team_id != self.team_id:
			raise ValidationError({"member": "Selected member does not belong to the selected team."})

		if self.member_id and getattr(self.member.user.profile, "role", None) != AccountProfile.ROLE_PLAYER:
			raise ValidationError({"member": "Only players can be rated in performance records."})

		if self.recorded_by_id and getattr(self.recorded_by.profile, "role", None) != AccountProfile.ROLE_COACH:
			raise ValidationError({"recorded_by": "Only coaches can record performance data."})

		if self.participation_status == self.PARTICIPATION_INJURED and not self.injury_status:
			raise ValidationError({"injury_status": "Please select the injury status for an injured player."})

		if self.participation_status != self.PARTICIPATION_INJURED:
			self.injury_status = ""

	def save(self, *args, **kwargs):
		self.full_clean()
		super().save(*args, **kwargs)

	def __str__(self):
		member_name = self.member.user.get_full_name() or self.member.user.email
		return f"{self.event.title} - {member_name}"
