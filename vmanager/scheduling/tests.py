from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from team_management.models import Team, TeamMembership

from .models import ScheduledEvent

User = get_user_model()


class SchedulingStaleObjectTests(TestCase):
    def setUp(self):
        self.coach = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="StrongPass123!",
            first_name="Coach",
            last_name="User",
        )
        AccountProfile.objects.create(user=self.coach, role=AccountProfile.ROLE_COACH)
        self.team = Team.objects.create(name="Lions", created_by=self.coach)
        TeamMembership.objects.create(
            user=self.coach,
            team=self.team,
            member_title="Coach",
            requested_role=AccountProfile.ROLE_COACH,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=self.coach,
        )
        self.client.force_login(self.coach)

    def _create_event(self):
        return ScheduledEvent.objects.create(
            team=self.team,
            created_by=self.coach,
            title="Practice",
            event_type=ScheduledEvent.TYPE_PRACTICE,
            scheduled_at="2026-04-10T10:00:00+03:00",
            duration_minutes=90,
            audience=ScheduledEvent.AUDIENCE_ALL,
        )

    def test_editing_removed_event_redirects_home(self):
        event = self._create_event()
        event_id = event.pk
        event.delete()

        response = self.client.get(reverse("scheduling:edit_event", args=[event_id]), follow=True)

        self.assertRedirects(response, reverse("scheduling:home"))
        self.assertContains(response, "This event was already removed.")

    def test_deleting_removed_event_redirects_home(self):
        event = self._create_event()
        event_id = event.pk
        event.delete()

        response = self.client.post(reverse("scheduling:delete_event", args=[event_id]), follow=True)

        self.assertRedirects(response, reverse("scheduling:home"))
        self.assertContains(response, "This event was already removed.")

    def test_event_detail_for_removed_event_returns_json_404(self):
        event = self._create_event()
        event_id = event.pk
        event.delete()

        response = self.client.get(reverse("scheduling:event_detail", args=[event_id]))

        self.assertEqual(response.status_code, 404)
        self.assertJSONEqual(response.content, {"error": "Event not found"})
