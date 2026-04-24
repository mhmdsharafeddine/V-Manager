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


class AnnouncementComment(models.Model):
	announcement = models.ForeignKey(
		Announcement,
		on_delete=models.CASCADE,
		related_name="comments",
	)
	author = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="announcement_comments",
	)
	parent = models.ForeignKey(
		"self",
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name="replies",
	)
	body = models.TextField(max_length=2000)
	created_at = models.DateTimeField(auto_now_add=True, db_index=True)

	class Meta:
		ordering = ["created_at", "id"]
		indexes = [
			models.Index(fields=["announcement", "parent", "created_at"]),
		]

	def __str__(self):
		return f"comment={self.id} ann={self.announcement_id} author={self.author_id}"


class PrivateMessage(models.Model):
	"""A direct message from one user to another within the same team context."""

	sender = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="sent_private_messages",
	)
	recipient = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="received_private_messages",
	)
	team = models.ForeignKey(
		Team,
		on_delete=models.CASCADE,
		related_name="private_messages",
	)
	body = models.TextField(max_length=4000)
	created_at = models.DateTimeField(auto_now_add=True, db_index=True)
	read_at = models.DateTimeField(null=True, blank=True, db_index=True)

	class Meta:
		ordering = ["created_at", "id"]
		indexes = [
			# Fetch the full conversation between two users quickly
			models.Index(fields=["team", "sender", "recipient", "created_at"]),
			# Unread-count queries per recipient
			models.Index(fields=["recipient", "read_at"]),
		]

	def __str__(self):
		return f"pm={self.id} {self.sender_id}→{self.recipient_id}"


class DigestEmailQueue(models.Model):
	"""
	Announcement emails that should not be delivered immediately.
	Two reasons:
	  - 'digest'     : user's announcement_digest preference is 'daily' (send at 22:00)
	  - 'quiet_hold' : announcement arrived during quiet hours with skip_entirely=False
	                   (hold until quiet hours end)
	The management command `send_digest_emails` processes this table.
	"""

	REASON_DIGEST = "digest"
	REASON_QUIET_HOLD = "quiet_hold"
	REASON_CHOICES = [
		(REASON_DIGEST, "Daily digest"),
		(REASON_QUIET_HOLD, "Quiet hours hold"),
	]

	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="digest_email_queue",
	)
	announcement = models.ForeignKey(
		Announcement,
		on_delete=models.CASCADE,
		related_name="digest_email_queue",
	)
	reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=REASON_DIGEST)
	# When the email becomes eligible to send (22:00 for digest, quiet-end for hold)
	send_after = models.DateTimeField(db_index=True)
	queued_at = models.DateTimeField(auto_now_add=True)
	sent_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		unique_together = [("user", "announcement")]
		ordering = ["send_after"]

	def __str__(self):
		return f"DigestQueue user={self.user_id} ann={self.announcement_id} after={self.send_after}"
