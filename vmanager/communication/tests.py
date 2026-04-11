from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from team_management.models import Team, TeamMembership

from .models import (
	Announcement,
	AnnouncementMessage,
	AnnouncementMessageRead,
	AnnouncementRecipient,
	UserPresence,
	UserPresenceConnection,
)


class CommunicationHubBackendTests(TestCase):
	def setUp(self):
		self.User = get_user_model()
		self.team = Team.objects.create(name="Phoenix FC")
		self.manager = self._create_team_user("manager", AccountProfile.ROLE_MANAGER, phone_number="+15550001")
		self.player = self._create_team_user("player", AccountProfile.ROLE_PLAYER, phone_number="+15550002")
		self.coach = self._create_team_user("coach", AccountProfile.ROLE_COACH, phone_number="")

	def _create_team_user(self, username, role, phone_number=""):
		user = self.User.objects.create_user(
			username=username,
			email=f"{username}@example.com",
			password="pass1234",
			first_name=username.capitalize(),
		)
		AccountProfile.objects.create(user=user, role=role, phone_number=phone_number)
		TeamMembership.objects.create(
			user=user,
			team=self.team,
			status=TeamMembership.STATUS_APPROVED,
			is_active=True,
		)
		return user

	def _create_announcement(
		self,
		*,
		title,
		audience=Announcement.AUDIENCE_ALL,
		created_by=None,
		pin_to_top=False,
		send_push_notification=True,
	):
		announcement = Announcement.objects.create(
			team=self.team,
			created_by=created_by or self.manager,
			title=title,
			body=f"Body for {title}",
			audience=audience,
			priority=Announcement.PRIORITY_INFO,
			pin_to_top=pin_to_top,
			send_push_notification=send_push_notification,
		)
		recipient_ids = {
			self.manager.id,
			self.player.id,
			self.coach.id,
		}
		if audience == Announcement.AUDIENCE_PLAYERS:
			recipient_ids = {self.manager.id, self.player.id}
		elif audience == Announcement.AUDIENCE_COACHES:
			recipient_ids = {self.manager.id, self.coach.id}

		AnnouncementRecipient.objects.bulk_create(
			[
				AnnouncementRecipient(
					announcement=announcement,
					user_id=recipient_id,
					read_at=timezone.now() if recipient_id == self.manager.id else None,
				)
				for recipient_id in recipient_ids
			]
		)
		return announcement

	def test_post_announcement_targets_recipients_and_feed_is_user_specific(self):
		self.client.force_login(self.manager)
		with self.captureOnCommitCallbacks(execute=True):
			response = self.client.post(
				reverse("communication:hub"),
				{
					"title": "Players meeting",
					"body": "Meet at 7 PM in the locker room.",
					"audience": Announcement.AUDIENCE_PLAYERS,
					"priority": Announcement.PRIORITY_IMPORTANT,
					"send_push_notification": "on",
					"send_email_notification": "on",
				},
			)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(Announcement.objects.count(), 1)

		announcement = Announcement.objects.first()
		recipient_ids = set(
			AnnouncementRecipient.objects.filter(announcement=announcement).values_list("user_id", flat=True)
		)
		self.assertSetEqual(recipient_ids, {self.manager.id, self.player.id})

		self.client.force_login(self.player)
		player_response = self.client.get(reverse("communication:hub"))
		player_titles = [item["title"] for item in player_response.context["announcements"]]
		self.assertIn("Players meeting", player_titles)

		self.client.force_login(self.coach)
		coach_response = self.client.get(reverse("communication:hub"))
		coach_titles = [item["title"] for item in coach_response.context["announcements"]]
		self.assertNotIn("Players meeting", coach_titles)

	def test_recent_announcements_are_limited_to_five(self):
		for index in range(7):
			self._create_announcement(title=f"Announcement {index}")

		self.client.force_login(self.player)
		response = self.client.get(reverse("communication:hub"))

		self.assertEqual(len(response.context["announcements"]), 5)
		titles = [item["title"] for item in response.context["announcements"]]
		self.assertIn("Announcement 6", titles)
		self.assertNotIn("Announcement 0", titles)

	def test_set_as_read_marks_only_target_announcement(self):
		first = self._create_announcement(title="First unread")
		second = self._create_announcement(title="Second unread")

		self.client.force_login(self.player)
		response = self.client.post(
			reverse("communication:mark_read", args=[first.id]),
			{"next": reverse("communication:hub")},
		)
		self.assertEqual(response.status_code, 302)

		self.assertTrue(
			AnnouncementRecipient.objects.filter(
				announcement=first,
				user=self.player,
				read_at__isnull=False,
			).exists()
		)
		self.assertTrue(
			AnnouncementRecipient.objects.filter(
				announcement=second,
				user=self.player,
				read_at__isnull=True,
			).exists()
		)

	def test_quick_stats_use_database_counts_unread_messages_and_avg_response_time(self):
		announcement = Announcement.objects.create(
			team=self.team,
			created_by=self.manager,
			title="Training update",
			body="Bring water bottles.",
			audience=Announcement.AUDIENCE_ALL,
			priority=Announcement.PRIORITY_INFO,
		)

		now = timezone.now()
		Announcement.objects.filter(pk=announcement.pk).update(created_at=now - timedelta(hours=2))

		AnnouncementRecipient.objects.bulk_create(
			[
				AnnouncementRecipient(announcement=announcement, user=self.manager, read_at=now),
				AnnouncementRecipient(announcement=announcement, user=self.player),
				AnnouncementRecipient(announcement=announcement, user=self.coach),
			]
		)

		first_reply = AnnouncementMessage.objects.create(
			announcement=announcement,
			author=self.player,
			body="Acknowledged.",
		)
		AnnouncementMessage.objects.filter(pk=first_reply.pk).update(created_at=now - timedelta(minutes=90))

		read_message = AnnouncementMessage.objects.create(
			announcement=announcement,
			author=self.coach,
			body="Seen by manager already.",
		)
		AnnouncementMessageRead.objects.create(message=read_message, user=self.manager)

		UserPresence.objects.update_or_create(user=self.manager, defaults={"last_seen": now})
		UserPresence.objects.update_or_create(user=self.player, defaults={"last_seen": now})
		UserPresence.objects.update_or_create(user=self.coach, defaults={})
		UserPresence.objects.filter(user=self.coach).update(last_seen=now - timedelta(minutes=11))

		self.client.force_login(self.manager)
		response = self.client.get(reverse("communication:hub"))
		quick_stats = response.context["quick_stats"]

		self.assertEqual(quick_stats["total_members"], 3)
		self.assertEqual(quick_stats["online_now"], 2)
		self.assertEqual(quick_stats["unread_messages"], 1)
		self.assertEqual(quick_stats["avg_response_time"], "30m")

	def test_weekly_stats_are_per_user_and_show_placeholders_when_empty(self):
		local_now = timezone.localtime(timezone.now())
		week_start = (local_now - timedelta(days=local_now.weekday())).replace(
			hour=0,
			minute=0,
			second=0,
			microsecond=0,
		)

		current_week = self._create_announcement(title="This week", audience=Announcement.AUDIENCE_ALL)
		old_week = self._create_announcement(title="Old week", audience=Announcement.AUDIENCE_ALL)
		coaches_only = self._create_announcement(title="Coach only", audience=Announcement.AUDIENCE_COACHES)

		Announcement.objects.filter(pk=current_week.pk).update(created_at=week_start + timedelta(hours=3))
		Announcement.objects.filter(pk=old_week.pk).update(created_at=week_start - timedelta(days=1))
		Announcement.objects.filter(pk=coaches_only.pk).update(created_at=week_start + timedelta(hours=2))

		AnnouncementRecipient.objects.filter(
			announcement=current_week,
			user=self.player,
		).update(read_at=timezone.now())

		self.client.force_login(self.player)
		player_response = self.client.get(reverse("communication:hub"))
		player_weekly_stats = player_response.context["weekly_stats"]
		self.assertEqual(player_weekly_stats["announcements_sent"], 1)
		self.assertEqual(player_weekly_stats["average_read_rate"], 100)

		self.client.force_login(self.coach)
		coach_response = self.client.get(reverse("communication:hub"))
		coach_weekly_stats = coach_response.context["weekly_stats"]
		self.assertEqual(coach_weekly_stats["announcements_sent"], 2)
		self.assertEqual(coach_weekly_stats["average_read_rate"], 0)

		no_team_user = self.User.objects.create_user(
			username="outsider",
			email="outsider@example.com",
			password="pass1234",
		)
		AccountProfile.objects.create(user=no_team_user, role=AccountProfile.ROLE_PLAYER)
		self.client.force_login(no_team_user)
		no_team_response = self.client.get(reverse("communication:hub"))

		self.assertIsNone(no_team_response.context["weekly_stats"]["announcements_sent"])
		self.assertIsNone(no_team_response.context["weekly_stats"]["average_read_rate"])

		html = no_team_response.content.decode("utf-8")
		self.assertIn('data-week-sent>--<', html)
		self.assertIn('data-week-read-rate>--</strong>', html)

	def test_manager_only_pin_and_pin_replacement_with_expiry(self):
		self.client.force_login(self.manager)
		with self.captureOnCommitCallbacks(execute=True):
			self.client.post(
				reverse("communication:hub"),
				{
					"title": "Pinned one",
					"body": "Pinned body one",
					"audience": Announcement.AUDIENCE_ALL,
					"priority": Announcement.PRIORITY_INFO,
					"pin_to_top": "on",
				},
			)

		first = Announcement.objects.get(title="Pinned one")
		self.assertTrue(first.pin_to_top)

		with self.captureOnCommitCallbacks(execute=True):
			self.client.post(
				reverse("communication:hub"),
				{
					"title": "Pinned two",
					"body": "Pinned body two",
					"audience": Announcement.AUDIENCE_ALL,
					"priority": Announcement.PRIORITY_INFO,
					"pin_to_top": "on",
				},
			)

		first.refresh_from_db()
		second = Announcement.objects.get(title="Pinned two")
		self.assertFalse(first.pin_to_top)
		self.assertTrue(second.pin_to_top)

		Announcement.objects.filter(pk=second.pk).update(created_at=timezone.now() - timedelta(days=3))
		self.client.get(reverse("communication:hub"))
		second.refresh_from_db()
		self.assertFalse(second.pin_to_top)

		self.client.force_login(self.coach)
		with self.captureOnCommitCallbacks(execute=True):
			self.client.post(
				reverse("communication:hub"),
				{
					"title": "Coach pin attempt",
					"body": "Coach body",
					"audience": Announcement.AUDIENCE_ALL,
					"priority": Announcement.PRIORITY_INFO,
					"pin_to_top": "on",
				},
			)

		coach_post = Announcement.objects.get(title="Coach pin attempt")
		self.assertFalse(coach_post.pin_to_top)

	def test_post_announcement_sends_email_notifications(self):
		mail.outbox = []
		self.client.force_login(self.manager)

		with self.captureOnCommitCallbacks(execute=True):
			response = self.client.post(
				reverse("communication:hub"),
				{
					"title": "Notify players",
					"body": "This should trigger notifications.",
					"audience": Announcement.AUDIENCE_PLAYERS,
					"priority": Announcement.PRIORITY_IMPORTANT,
					"send_email_notification": "on",
				},
			)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, [self.player.email])
		self.assertIn("New V-Manager announcement", mail.outbox[0].subject)
		self.assertFalse(Announcement.objects.get(title="Notify players").send_sms_notification)

	def test_push_announcements_appear_on_notifications_page(self):
		self._create_announcement(title="Push enabled", send_push_notification=True)
		self._create_announcement(title="Push disabled", send_push_notification=False)

		self.client.force_login(self.player)
		response = self.client.get(reverse("scheduling:notifications"))

		self.assertEqual(response.status_code, 200)
		announcement_titles = [
			item["event_title"]
			for item in response.context["notifications"]
			if item.get("kind") == "announcement"
		]
		self.assertIn("Push enabled", announcement_titles)
		self.assertNotIn("Push disabled", announcement_titles)

	def test_delete_announcement_notification_hides_it_without_deleting_feed_item(self):
		announcement = self._create_announcement(title="Delete only notification", send_push_notification=True)

		self.client.force_login(self.player)
		notifications_response = self.client.get(reverse("scheduling:notifications"))
		notification_items = [
			item
			for item in notifications_response.context["notifications"]
			if item.get("kind") == "announcement"
		]
		self.assertEqual(
			notification_items[0]["delete_action_url"],
			reverse("scheduling:delete_announcement_notification", args=[announcement.id]),
		)

		response = self.client.post(
			reverse("scheduling:delete_announcement_notification", args=[announcement.id]),
			{"next": reverse("scheduling:notifications")},
		)

		self.assertEqual(response.status_code, 302)
		self.assertTrue(
			AnnouncementRecipient.objects.filter(
				announcement=announcement,
				user=self.player,
				is_deleted=True,
			).exists()
		)

		notifications_response = self.client.get(reverse("scheduling:notifications"))
		announcement_titles = [
			item["event_title"]
			for item in notifications_response.context["notifications"]
			if item.get("kind") == "announcement"
		]
		self.assertNotIn("Delete only notification", announcement_titles)

		feed_response = self.client.get(reverse("communication:announcements"))
		feed_titles = [item["title"] for item in feed_response.context["announcements"]]
		self.assertIn("Delete only notification", feed_titles)

	def test_mark_all_notifications_marks_push_announcements_read(self):
		announcement = self._create_announcement(title="Needs read", send_push_notification=True)

		self.client.force_login(self.player)
		response = self.client.post(
			reverse("scheduling:mark_all_notifications_read"),
			{"next": reverse("scheduling:notifications")},
		)

		self.assertEqual(response.status_code, 302)
		self.assertTrue(
			AnnouncementRecipient.objects.filter(
				announcement=announcement,
				user=self.player,
				read_at__isnull=False,
			).exists()
		)

	def test_all_announcements_page_lists_full_feed(self):
		for index in range(8):
			self._create_announcement(title=f"Feed {index}")

		self.client.force_login(self.player)
		response = self.client.get(reverse("communication:announcements"))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["announcement_count"], 8)
		self.assertEqual(len(response.context["announcements"]), 8)

	def test_quick_stats_uses_live_presence_when_websocket_connections_exist(self):
		now = timezone.now()
		UserPresence.objects.update_or_create(user=self.manager, defaults={"last_seen": now})
		UserPresence.objects.update_or_create(user=self.player, defaults={"last_seen": now})
		UserPresence.objects.update_or_create(user=self.coach, defaults={"last_seen": now})

		UserPresenceConnection.objects.create(
			user=self.manager,
			team=self.team,
			channel_name="presence-manager",
			last_heartbeat_at=now,
		)
		UserPresenceConnection.objects.create(
			user=self.player,
			team=self.team,
			channel_name="presence-player",
			last_heartbeat_at=now,
		)

		self.client.force_login(self.manager)
		response = self.client.get(reverse("communication:hub"))
		self.assertEqual(response.context["quick_stats"]["online_now"], 2)
