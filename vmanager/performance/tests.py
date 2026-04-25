from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from datetime import datetime

from accounts.models import AccountProfile
from scheduling.models import EventAttendance, ScheduledEvent
from team_management.models import Team, TeamMembership

from .forms import TeamPerformanceRecordForm
from .models import TeamPerformanceRecord

User = get_user_model()


class PerformanceFormTests(TestCase):
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

        self.player = User.objects.create_user(
            username="player@example.com",
            email="player@example.com",
            password="StrongPass123!",
            first_name="Sami",
            last_name="Saab",
        )
        AccountProfile.objects.create(user=self.player, role=AccountProfile.ROLE_PLAYER)
        self.player_membership = TeamMembership.objects.create(
            user=self.player,
            team=self.team,
            member_title="Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=self.coach,
        )

        self.event = ScheduledEvent.objects.create(
            team=self.team,
            created_by=self.coach,
            title="Match 1",
            event_type=ScheduledEvent.TYPE_MATCH,
            scheduled_at="2026-04-01T10:10:00+03:00",
            duration_minutes=90,
            audience=ScheduledEvent.AUDIENCE_ALL,
        )

    def test_injured_participation_requires_injury_status(self):
        EventAttendance.objects.create(
            event=self.event,
            player=self.player,
            status=EventAttendance.STATUS_NOT_ATTENDING,
            not_attending_reason=EventAttendance.REASON_INJURED,
        )

        form = TeamPerformanceRecordForm(
            data={
                "event": self.event.pk,
                "member": self.player_membership.pk,
                "result": TeamPerformanceRecord.RESULT_WIN,
                "participation_status": TeamPerformanceRecord.PARTICIPATION_INJURED,
                "injury_status": "",
                "points_scored": 0,
                "points_conceded": 0,
                "target_score": "",
                "target_achieved": "",
                "kills": 0,
                "aces": 0,
                "blocks": 0,
                "assists": 0,
                "digs": 0,
                "unforced_errors": 0,
                "notes": "",
            },
            team=self.team,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("injury_status", form.errors)

    def test_non_injured_participation_clears_injury_status(self):
        EventAttendance.objects.create(
            event=self.event,
            player=self.player,
            status=EventAttendance.STATUS_ATTENDING,
        )

        form = TeamPerformanceRecordForm(
            data={
                "event": self.event.pk,
                "member": self.player_membership.pk,
                "result": TeamPerformanceRecord.RESULT_WIN,
                "participation_status": TeamPerformanceRecord.PARTICIPATION_PRESENT,
                "injury_status": TeamPerformanceRecord.INJURY_MINOR_ISSUE,
                "points_scored": 0,
                "points_conceded": 0,
                "target_score": "",
                "target_achieved": "",
                "kills": 0,
                "aces": 0,
                "blocks": 0,
                "assists": 0,
                "digs": 0,
                "unforced_errors": 0,
                "notes": "",
            },
            team=self.team,
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["injury_status"], "")


class PerformanceStaleObjectTests(TestCase):
    def setUp(self):
        self.coach = User.objects.create_user(
            username="coach2@example.com",
            email="coach2@example.com",
            password="StrongPass123!",
            first_name="Coach",
            last_name="Two",
        )
        AccountProfile.objects.create(user=self.coach, role=AccountProfile.ROLE_COACH)
        self.team = Team.objects.create(name="Falcons", created_by=self.coach)
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
            username="player2@example.com",
            email="player2@example.com",
            password="StrongPass123!",
            first_name="Sami",
            last_name="Saab",
        )
        AccountProfile.objects.create(user=self.player, role=AccountProfile.ROLE_PLAYER)
        self.player_membership = TeamMembership.objects.create(
            user=self.player,
            team=self.team,
            member_title="Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=self.coach,
        )

        self.event = ScheduledEvent.objects.create(
            team=self.team,
            created_by=self.coach,
            title="Match 2",
            event_type=ScheduledEvent.TYPE_MATCH,
            scheduled_at="2026-04-11T10:10:00+03:00",
            duration_minutes=90,
            audience=ScheduledEvent.AUDIENCE_ALL,
        )
        self.client.force_login(self.coach)

    def test_editing_removed_record_redirects_dashboard(self):
        record = TeamPerformanceRecord.objects.create(
            team=self.team,
            event=self.event,
            member=self.player_membership,
            recorded_by=self.coach,
            result=TeamPerformanceRecord.RESULT_WIN,
            participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT,
        )
        record_id = record.pk
        record.delete()

        response = self.client.get(reverse("performance:record_edit", args=[record_id]), follow=True)

        self.assertRedirects(response, reverse("performance:dashboard"))
        self.assertContains(response, "This performance record was already removed.")


class PerformanceDashboardSummaryApiTests(TestCase):
    def setUp(self):
        self.coach = User.objects.create_user(
            username="coach3@example.com",
            email="coach3@example.com",
            password="StrongPass123!",
            first_name="Coach",
            last_name="Three",
        )
        AccountProfile.objects.create(user=self.coach, role=AccountProfile.ROLE_COACH)
        self.team = Team.objects.create(name="Sharks", created_by=self.coach)
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
            username="player3@example.com",
            email="player3@example.com",
            password="StrongPass123!",
            first_name="Maya",
            last_name="Lee",
        )
        AccountProfile.objects.create(user=self.player, role=AccountProfile.ROLE_PLAYER)
        self.player_membership = TeamMembership.objects.create(
            user=self.player,
            team=self.team,
            member_title="Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            added_by=self.coach,
        )

        tz = timezone.get_current_timezone()
        prev_month_event = ScheduledEvent.objects.create(
            team=self.team,
            created_by=self.coach,
            title="Prev Month Match",
            event_type=ScheduledEvent.TYPE_MATCH,
            scheduled_at=timezone.make_aware(datetime(2026, 3, 10, 10, 0), tz),
            duration_minutes=90,
            audience=ScheduledEvent.AUDIENCE_ALL,
        )
        this_month_event = ScheduledEvent.objects.create(
            team=self.team,
            created_by=self.coach,
            title="This Month Match",
            event_type=ScheduledEvent.TYPE_MATCH,
            scheduled_at=timezone.make_aware(datetime(2026, 4, 10, 10, 0), tz),
            duration_minutes=90,
            audience=ScheduledEvent.AUDIENCE_ALL,
        )

        TeamPerformanceRecord.objects.create(
            team=self.team,
            event=prev_month_event,
            member=self.player_membership,
            recorded_by=self.coach,
            result=TeamPerformanceRecord.RESULT_LOSS,
            participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT,
            target_achieved=False,
            points_scored=15,
            unforced_errors=8,
        )
        TeamPerformanceRecord.objects.create(
            team=self.team,
            event=this_month_event,
            member=self.player_membership,
            recorded_by=self.coach,
            result=TeamPerformanceRecord.RESULT_WIN,
            participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT,
            target_achieved=True,
            points_scored=25,
            unforced_errors=3,
        )

        self.client.force_login(self.coach)

    def test_summary_endpoint_month_range_returns_previous_month_and_trends(self):
        response = self.client.get(
            reverse("performance:dashboard_summary"),
            {"range": "month", "month": "2026-04"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["range"], "month")
        self.assertEqual(payload["month"], "2026-04")
        self.assertEqual(payload["summary"]["wins"], 1)
        self.assertEqual(payload["summary"]["losses"], 0)
        self.assertEqual(payload["previous_month_summary"]["wins"], 0)
        self.assertEqual(payload["previous_month_summary"]["losses"], 1)
        self.assertEqual(payload["trends"]["wins"]["direction"], "up")
        self.assertEqual(payload["trends"]["losses"]["direction"], "down")

    def test_summary_endpoint_last_match_range_returns_single_event_view(self):
        response = self.client.get(reverse("performance:dashboard_summary"), {"range": "last_match"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["range"], "last_match")
        self.assertEqual(payload["summary"]["total_events"], 1)
