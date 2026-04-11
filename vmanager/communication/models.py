from django.conf import settings
from django.db import models

from team_management.models import Team


class Announcement(models.Model):
	PRIORITY_INFO = "info"
	PRIORITY_IMPORTANT = "important"
	PRIORITY_URGENT = "urgent"
	PRIORITY_CHOICES = [
		(PRIORITY_INFO, "Info"),
		(PRIORITY_IMPORTANT, "Important"),
		(PRIORITY_URGENT, "Urgent"),
	]

	AUDIENCE_ALL = "all"
	AUDIENCE_PLAYERS = "players"
	AUDIENCE_COACHES = "coaches"
	AUDIENCE_STAFF = "staff"
	AUDIENCE_PARENTS = "parents"
	AUDIENCE_MANAGERS = "managers"
	AUDIENCE_CHOICES = [
		(AUDIENCE_ALL, "All Team"),
		(AUDIENCE_PLAYERS, "Players Only"),
		(AUDIENCE_COACHES, "Coaches Only"),
		(AUDIENCE_STAFF, "Staff Only"),
		(AUDIENCE_PARENTS, "Parents Only"),
		(AUDIENCE_MANAGERS, "Managers Only"),
	]

	team = models.ForeignKey(
		Team,
		on_delete=models.CASCADE,
		related_name="announcements",
		null=True,
		blank=True,
	)
	created_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="announcements_created",
	)
	title = models.CharField(max_length=180)
	body = models.TextField()
	audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default=AUDIENCE_ALL)
	priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_INFO)
	send_push_notification = models.BooleanField(default=True)
	send_email_notification = models.BooleanField(default=True)
	send_sms_notification = models.BooleanField(default=False)
	pin_to_top = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True, db_index=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-pin_to_top", "-created_at", "-id"]

	def __str__(self):
		return f"{self.title} ({self.get_audience_display()})"


class AnnouncementRecipient(models.Model):
	announcement = models.ForeignKey(
		Announcement,
		on_delete=models.CASCADE,
		related_name="recipients",
	)
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="announcement_recipients",
	)
	delivered_at = models.DateTimeField(auto_now_add=True)
	read_at = models.DateTimeField(null=True, blank=True, db_index=True)
	is_deleted = models.BooleanField(default=False, db_index=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["announcement", "user"],
				name="uniq_announcement_recipient",
			)
		]
		indexes = [
			models.Index(fields=["user", "read_at"]),
			models.Index(fields=["announcement", "read_at"]),
		]

	def __str__(self):
		return f"announcement={self.announcement_id} user={self.user_id}"


class AnnouncementMessage(models.Model):
	announcement = models.ForeignKey(
		Announcement,
		on_delete=models.CASCADE,
		related_name="messages",
	)
	author = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="announcement_messages",
	)
	body = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True, db_index=True)

	class Meta:
		ordering = ["created_at", "id"]

	def __str__(self):
		return f"announcement={self.announcement_id} author={self.author_id}"


class AnnouncementMessageRead(models.Model):
	message = models.ForeignKey(
		AnnouncementMessage,
		on_delete=models.CASCADE,
		related_name="read_states",
	)
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="announcement_message_reads",
	)
	read_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["message", "user"],
				name="uniq_announcement_message_read",
			)
		]
		indexes = [models.Index(fields=["user", "read_at"])]

	def __str__(self):
		return f"message={self.message_id} user={self.user_id}"


class UserPresence(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="presence",
	)
	last_seen = models.DateTimeField(auto_now=True, db_index=True)

	class Meta:
		verbose_name_plural = "user presences"

	def __str__(self):
		return f"user={self.user_id}"


class UserPresenceConnection(models.Model):
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="presence_connections",
	)
	team = models.ForeignKey(
		Team,
		on_delete=models.CASCADE,
		related_name="presence_connections",
	)
	channel_name = models.CharField(max_length=255, unique=True)
	connected_at = models.DateTimeField(auto_now_add=True)
	last_heartbeat_at = models.DateTimeField(auto_now_add=True, db_index=True)
	disconnected_at = models.DateTimeField(null=True, blank=True, db_index=True)

	class Meta:
		indexes = [
			models.Index(fields=["team", "disconnected_at", "last_heartbeat_at"]),
			models.Index(fields=["user", "disconnected_at"]),
		]

	def __str__(self):
		return f"user={self.user_id} channel={self.channel_name}"
