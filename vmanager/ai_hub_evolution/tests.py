from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from performance.models import TeamPerformanceRecord
from scheduling.models import ScheduledEvent
from team_management.models import Team, TeamMembership

User = get_user_model()


class AiHubHomeTests(TestCase):
    def setUp(self):
        self.coach = User.objects.create_user(
            username="coach-ai@example.com",
            email="coach-ai@example.com",
            password="StrongPass123!",
            first_name="Coach",
            last_name="User",
        )
        AccountProfile.objects.create(user=self.coach, role=AccountProfile.ROLE_COACH)
        self.team = Team.objects.create(name="Waves", created_by=self.coach)
        TeamMembership.objects.create(
            user=self.coach,
            team=self.team,
            member_title="Coach",
            requested_role=AccountProfile.ROLE_COACH,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=self.coach,
        )

        self.player = User.objects.create_user(
            username="player-ai@example.com",
            email="player-ai@example.com",
            password="StrongPass123!",
            first_name="April",
            last_name="Player",
        )
        AccountProfile.objects.create(
            user=self.player,
            role=AccountProfile.ROLE_PLAYER,
            position="Opposite Hitter",
        )
        self.player_membership = TeamMembership.objects.create(
            user=self.player,
            team=self.team,
            member_title="Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=self.coach,
        )

        april_event = ScheduledEvent.objects.create(
            team=self.team,
            created_by=self.coach,
            title="April Showcase",
            event_type=ScheduledEvent.TYPE_MATCH,
            scheduled_at=timezone.make_aware(datetime(2026, 4, 18, 18, 0)),
            duration_minutes=90,
            audience=ScheduledEvent.AUDIENCE_ALL,
        )
        TeamPerformanceRecord.objects.create(
            team=self.team,
            event=april_event,
            member=self.player_membership,
            recorded_by=self.coach,
            result=TeamPerformanceRecord.RESULT_WIN,
            participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT,
            kills=7,
            aces=2,
            blocks=1,
        )

        self.client.force_login(self.coach)

    def test_home_includes_player_with_april_record(self):
        response = self.client.get(reverse("ai_hub_home"))

        self.assertEqual(response.status_code, 200)
        players = response.context["players"]
        player_names = {player["name"] for player in players}

        self.assertIn("April Player", player_names)
