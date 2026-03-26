from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile

from .models import TeamMembership

User = get_user_model()


class TeamManagementTests(TestCase):
    def _create_user(self, *, email, role, club_name=""):
        user = User.objects.create_user(
            username=email,
            email=email,
            password="StrongPass123!",
            first_name=email.split("@")[0].title(),
            last_name="User",
        )
        AccountProfile.objects.create(user=user, role=role, club_name=club_name)
        return user

    def test_manager_roster_access_creates_team_and_self_membership(self):
        manager = self._create_user(
            email="manager@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Lions Volleyball Club",
        )
        self.client.force_login(manager)

        response = self.client.get(reverse("team_management:roster"))

        self.assertEqual(response.status_code, 200)
        membership = TeamMembership.objects.get(user=manager)
        self.assertEqual(membership.team.name, "Lions Volleyball Club")
        self.assertContains(response, "Team Roster")

    def test_staff_roster_access_does_not_auto_create_team(self):
        staff = self._create_user(email="staff@example.com", role=AccountProfile.ROLE_STAFF)
        self.client.force_login(staff)

        response = self.client.get(reverse("team_management:roster"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamMembership.objects.filter(user=staff).exists())
        self.assertContains(response, "You are not assigned to a team roster yet.")

    def test_staff_without_team_cannot_access_add_member_page(self):
        staff = self._create_user(email="staff2@example.com", role=AccountProfile.ROLE_STAFF)
        self.client.force_login(staff)

        response = self.client.get(reverse("team_management:add_member"))

        self.assertRedirects(response, reverse("team_management:roster"))

    def test_player_can_view_roster_but_not_manage_it(self):
        manager = self._create_user(
            email="manager2@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Falcons Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player = self._create_user(email="player@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)

        self.client.force_login(player)
        response = self.client.get(reverse("team_management:roster"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Add Member")

    def test_player_cannot_access_add_member_page(self):
        player = self._create_user(email="player2@example.com", role=AccountProfile.ROLE_PLAYER)
        self.client.force_login(player)

        response = self.client.get(reverse("team_management:add_member"))

        self.assertRedirects(response, reverse("team_management:roster"))

    def test_manager_can_add_existing_unassigned_player(self):
        manager = self._create_user(
            email="manager3@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Sharks Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player = self._create_user(email="freshplayer@example.com", role=AccountProfile.ROLE_PLAYER)

        response = self.client.post(
            reverse("team_management:add_member"),
            data={
                "member_category": "player",
                "first_name": "Fresh",
                "last_name": "Player",
                "email": "freshplayer@example.com",
                "phone_number": "+961111111",
                "date_of_birth": "2005-04-06",
                "jersey_number": "8",
                "position": "Setter",
                "member_title": "Player",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        membership = TeamMembership.objects.get(user=player)
        self.assertEqual(membership.team, team)
        player.refresh_from_db()
        self.assertEqual(player.profile.position, "Setter")

    def test_manager_cannot_add_member_already_on_other_team(self):
        manager_a = self._create_user(
            email="managera@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Alpha Club",
        )
        manager_b = self._create_user(
            email="managerb@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Beta Club",
        )
        self.client.force_login(manager_a)
        self.client.get(reverse("team_management:roster"))
        team_a = TeamMembership.objects.get(user=manager_a).team

        self.client.force_login(manager_b)
        self.client.get(reverse("team_management:roster"))

        player = self._create_user(email="taken@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=player, team=team_a, member_title="Player", added_by=manager_a)

        response = self.client.post(
            reverse("team_management:add_member"),
            data={
                "member_category": "player",
                "first_name": "Taken",
                "last_name": "Player",
                "email": "taken@example.com",
                "position": "Outside Hitter",
                "member_title": "Player",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already assigned to another team")

    def test_manager_can_add_existing_staff_without_player_fields(self):
        manager = self._create_user(
            email="leadmanager@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Delta Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        staff = self._create_user(email="coachmember@example.com", role=AccountProfile.ROLE_COACH)

        response = self.client.post(
            reverse("team_management:add_member"),
            data={
                "member_category": "staff",
                "first_name": "Coach",
                "last_name": "Member",
                "email": "coachmember@example.com",
                "phone_number": "+961333333",
                "date_of_birth": "1990-07-04",
            },
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        membership = TeamMembership.objects.get(user=staff)
        self.assertEqual(membership.team, team)
        self.assertEqual(membership.member_title, "Coach")
        staff.refresh_from_db()
        self.assertEqual(staff.profile.position, "")
        self.assertIsNone(staff.profile.jersey_number)

    def test_summary_cards_count_only_active_members(self):
        manager = self._create_user(
            email="manager4@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Orcas Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        active_player = self._create_user(email="activeplayer@example.com", role=AccountProfile.ROLE_PLAYER)
        inactive_captain = self._create_user(email="inactivecaptain@example.com", role=AccountProfile.ROLE_PLAYER)
        active_coach = self._create_user(email="activecoach@example.com", role=AccountProfile.ROLE_COACH)

        TeamMembership.objects.create(user=active_player, team=team, member_title="Player", added_by=manager, is_active=True)
        TeamMembership.objects.create(user=inactive_captain, team=team, member_title="Captain", added_by=manager, is_active=False)
        TeamMembership.objects.create(user=active_coach, team=team, member_title="Coach", added_by=manager, is_active=True)

        response = self.client.get(reverse("team_management:roster"))

        summary_cards = response.context["summary_cards"]
        counts = {card["label"]: card["count"] for card in summary_cards}
        self.assertEqual(counts["Players"], 1)
        self.assertEqual(counts["Team Captains"], 0)
        self.assertEqual(counts["Coaches"], 1)

    def test_roster_status_filter_filters_members(self):
        manager = self._create_user(
            email="manager5@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Waves Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        active_player = self._create_user(email="activeonly@example.com", role=AccountProfile.ROLE_PLAYER)
        inactive_player = self._create_user(email="inactiveonly@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=active_player, team=team, member_title="Player", added_by=manager, is_active=True)
        TeamMembership.objects.create(user=inactive_player, team=team, member_title="Player", added_by=manager, is_active=False)

        response = self.client.get(reverse("team_management:roster"), {"status": "inactive"})

        members = response.context["members"]
        self.assertEqual(response.context["status_filter"], "inactive")
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["user"].email, "inactiveonly@example.com")

    def test_manager_can_add_parent_account_as_player_using_parent_email(self):
        manager = self._create_user(
            email="manager6@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Orbit Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        parent = self._create_user(email="parent@example.com", role=AccountProfile.ROLE_PARENT)
        parent.profile.child_name = "Maya Salem"
        parent.profile.save(update_fields=["child_name"])

        response = self.client.post(
            reverse("team_management:add_member"),
            data={
                "member_category": "player",
                "first_name": "Maya",
                "last_name": "Salem",
                "email": "parent@example.com",
                "phone_number": "+96170000000",
                "date_of_birth": "2011-09-10",
                "jersey_number": "12",
                "position": "Setter",
                "member_title": "Player",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        membership = TeamMembership.objects.get(user=parent)
        self.assertEqual(membership.team, team)
        parent.profile.refresh_from_db()
        self.assertEqual(parent.profile.child_name, "Maya Salem")
        self.assertEqual(parent.profile.position, "Setter")

        roster_response = self.client.get(reverse("team_management:roster"))
        self.assertContains(roster_response, "Maya Salem")
        self.assertContains(roster_response, "parent@example.com")

    def test_parent_added_to_roster_can_view_it(self):
        manager = self._create_user(
            email="manager7@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Stars Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        parent = self._create_user(email="parent2@example.com", role=AccountProfile.ROLE_PARENT)
        parent.profile.child_name = "Lina Haddad"
        parent.profile.save(update_fields=["child_name"])
        TeamMembership.objects.create(user=parent, team=team, member_title="Player", added_by=manager)

        self.client.force_login(parent)
        response = self.client.get(reverse("team_management:roster"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lina Haddad")
