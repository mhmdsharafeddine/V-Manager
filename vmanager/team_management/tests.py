from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile, ParentChildLink

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

    def test_manager_roster_page_links_to_account_creation_page(self):
        manager = self._create_user(
            email="rostermanager@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Roster Club",
        )
        self.client.force_login(manager)

        response = self.client.get(reverse("team_management:roster"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Account")
        self.assertNotContains(response, "Create Roster Account")

    def test_staff_account_creation_page_hides_staff_option(self):
        manager = self._create_user(
            email="leadstaffform@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Orbit Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        staff = self._create_user(email="staffform@example.com", role=AccountProfile.ROLE_STAFF)
        TeamMembership.objects.create(user=staff, team=team, member_title="Staff", added_by=manager)

        self.client.force_login(staff)
        response = self.client.get(reverse("team_management:invite_member"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Roster Account")
        self.assertNotContains(response, '<option value="staff">Staff</option>', html=False)

    def test_player_cannot_access_add_member_page(self):
        player = self._create_user(email="player2@example.com", role=AccountProfile.ROLE_PLAYER)
        self.client.force_login(player)

        response = self.client.get(reverse("team_management:add_member"))

        self.assertRedirects(response, reverse("home"))

    def test_captain_cannot_see_or_access_ai_hub_and_match_readiness(self):
        manager = self._create_user(
            email="managercaptain@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Captain Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        captain = self._create_user(email="captainhidden@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=captain, team=team, member_title="Captain", added_by=manager)

        self.client.force_login(captain)
        home_response = self.client.get(reverse("home"))
        self.assertContains(home_response, "Match Readiness Score")
        self.assertContains(home_response, "AI Evolution Hub")
        self.assertContains(home_response, "Restricted")

        match_response = self.client.get(reverse("match_readiness"), follow=True)
        self.assertRedirects(match_response, reverse("home"))
        self.assertContains(match_response, "cannot access AI Evolution Hub or Match Readiness")

        ai_response = self.client.get(reverse("ai_hub_home"), follow=True)
        self.assertRedirects(ai_response, reverse("home"))
        self.assertContains(ai_response, "cannot access AI Evolution Hub or Match Readiness")

    def test_parent_cannot_see_or_access_ai_hub_and_match_readiness(self):
        manager = self._create_user(
            email="managerparenthidden@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Parent Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        parent = self._create_user(email="parenthidden@example.com", role=AccountProfile.ROLE_PARENT)
        TeamMembership.objects.create(user=parent, team=team, member_title="Parent", added_by=manager)

        self.client.force_login(parent)
        home_response = self.client.get(reverse("home"))
        self.assertContains(home_response, "Match Readiness Score")
        self.assertContains(home_response, "AI Evolution Hub")
        self.assertContains(home_response, "Restricted")

        match_response = self.client.get(reverse("match_readiness"), follow=True)
        self.assertRedirects(match_response, reverse("home"))
        self.assertContains(match_response, "cannot access AI Evolution Hub or Match Readiness")

    def test_manager_can_invite_staff_from_roster(self):
        manager = self._create_user(
            email="managerinvite@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Invite Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        response = self.client.post(
            reverse("team_management:invite_member"),
            data={
                "invite-first_name": "Sara",
                "invite-last_name": "Staff",
                "invite-email": "sarastaff@example.com",
                "invite-phone_number": "+961123456",
                "invite-date_of_birth": "1993-05-22",
                "invite-invite_role": "staff",
                "invite-position": "",
                "invite-jersey_number": "",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "was added to the roster and emailed a temporary password")
        invited_user = User.objects.get(email="sarastaff@example.com")
        self.assertEqual(invited_user.profile.role, AccountProfile.ROLE_STAFF)
        self.assertTrue(invited_user.profile.must_change_password)
        self.assertEqual(invited_user.team_membership.team, team)
        self.assertEqual(invited_user.team_membership.member_title, "Staff")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Temporary password:", mail.outbox[0].body)

    def test_invite_member_duplicate_email_shows_validation_error(self):
        manager = self._create_user(
            email="managerduplicate@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Duplicate Club",
        )
        existing_user = self._create_user(email="existinginvite@example.com", role=AccountProfile.ROLE_PLAYER)
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))

        response = self.client.post(
            reverse("team_management:invite_member"),
            data={
                "invite-first_name": "Existing",
                "invite-last_name": "Invite",
                "invite-email": existing_user.email,
                "invite-phone_number": "",
                "invite-date_of_birth": "",
                "invite-invite_role": "player",
                "invite-position": "Setter",
                "invite-jersey_number": "7",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists.")
        self.assertNotContains(response, "We could not send the invite email right now")

    def test_staff_can_invite_coach_but_not_staff(self):
        manager = self._create_user(
            email="managerlead@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Crew Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        staff = self._create_user(email="crewstaff@example.com", role=AccountProfile.ROLE_STAFF)
        TeamMembership.objects.create(user=staff, team=team, member_title="Staff", added_by=manager)

        self.client.force_login(staff)
        blocked_response = self.client.post(
            reverse("team_management:invite_member"),
            data={
                "invite-first_name": "Blocked",
                "invite-last_name": "Staff",
                "invite-email": "blockedstaff@example.com",
                "invite-phone_number": "",
                "invite-date_of_birth": "",
                "invite-invite_role": "staff",
                "invite-position": "",
                "invite-jersey_number": "",
            },
        )

        self.assertEqual(blocked_response.status_code, 200)
        self.assertContains(blocked_response, "Select a valid choice.")
        self.assertFalse(User.objects.filter(email="blockedstaff@example.com").exists())

        allowed_response = self.client.post(
            reverse("team_management:invite_member"),
            data={
                "invite-first_name": "Lina",
                "invite-last_name": "Coach",
                "invite-email": "linacoach@example.com",
                "invite-phone_number": "",
                "invite-date_of_birth": "",
                "invite-invite_role": "coach",
                "invite-position": "",
                "invite-jersey_number": "",
            },
            follow=True,
        )

        self.assertRedirects(allowed_response, reverse("team_management:roster"))
        self.assertTrue(User.objects.filter(email="linacoach@example.com").exists())
        self.assertEqual(User.objects.get(email="linacoach@example.com").profile.role, AccountProfile.ROLE_COACH)

    def test_inactive_player_cannot_access_roster(self):
        manager = self._create_user(
            email="managerinactive@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Echo Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player = self._create_user(email="inactiveplayer@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(
            user=player,
            team=team,
            member_title="Player",
            added_by=manager,
            is_active=False,
        )

        self.client.force_login(player)
        response = self.client.get(reverse("team_management:roster"), follow=True)

        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, "Your team access is inactive.")

        home_response = self.client.get(reverse("home"))
        self.assertNotContains(home_response, 'href="/team/roster/"', html=False)
        self.assertContains(home_response, "Inactive Access")

    def test_inactive_manager_cannot_access_roster(self):
        manager = self._create_user(
            email="managerblocked@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Pulse Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        membership = TeamMembership.objects.get(user=manager)
        membership.is_active = False
        membership.save(update_fields=["is_active"])

        response = self.client.get(reverse("team_management:roster"), follow=True)

        self.assertRedirects(response, reverse("home"))
        self.assertContains(response, "Your team access is inactive.")

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
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "Player added.")
        membership = TeamMembership.objects.get(user=player)
        self.assertEqual(membership.team, team)
        self.assertTrue(membership.is_active)
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
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "Coach added.")
        membership = TeamMembership.objects.get(user=staff)
        self.assertEqual(membership.team, team)
        self.assertEqual(membership.member_title, "Coach")
        self.assertTrue(membership.is_active)
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

    def test_roster_member_type_filter_filters_roles(self):
        manager = self._create_user(
            email="managerfilter@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Role Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        coach = self._create_user(email="coachfilter@example.com", role=AccountProfile.ROLE_COACH)
        staff = self._create_user(email="stafffilter@example.com", role=AccountProfile.ROLE_STAFF)
        captain = self._create_user(email="captainfilter@example.com", role=AccountProfile.ROLE_PLAYER)

        TeamMembership.objects.create(user=coach, team=team, member_title="Coach", added_by=manager)
        TeamMembership.objects.create(user=staff, team=team, member_title="Staff", added_by=manager)
        TeamMembership.objects.create(user=captain, team=team, member_title="Captain", added_by=manager)

        response = self.client.get(reverse("team_management:roster"), {"member_type": "captains"})

        members = response.context["members"]
        self.assertEqual(response.context["member_type_filter"], "captains")
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["user"].email, "captainfilter@example.com")

    def test_roster_sort_filter_orders_members(self):
        manager = self._create_user(
            email="managersort@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Sort Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        zara = self._create_user(email="zara@example.com", role=AccountProfile.ROLE_PLAYER)
        adam = self._create_user(email="adam@example.com", role=AccountProfile.ROLE_PLAYER)

        zara_membership = TeamMembership.objects.create(user=zara, team=team, member_title="Player", added_by=manager)
        adam_membership = TeamMembership.objects.create(user=adam, team=team, member_title="Player", added_by=manager)

        alphabetical_response = self.client.get(reverse("team_management:roster"), {"sort": "alphabetical"})
        alphabetical_emails = [member["user"].email for member in alphabetical_response.context["members"]]
        self.assertLess(alphabetical_emails.index("adam@example.com"), alphabetical_emails.index("zara@example.com"))

        newest_response = self.client.get(reverse("team_management:roster"), {"sort": "newest"})
        newest_members = newest_response.context["members"]
        self.assertEqual(newest_response.context["sort_filter"], "newest")
        self.assertEqual(newest_members[0]["membership"].pk, adam_membership.pk)
        self.assertEqual(newest_members[1]["membership"].pk, zara_membership.pk)

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
                "first_name": "Rola",
                "last_name": "Itani",
                "email": "parent@example.com",
                "phone_number": "+96170000000",
                "date_of_birth": "1987-09-10",
                "jersey_number": "12",
                "position": "Setter",
                "member_title": "Parent",
                "is_active": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "Player added.")
        membership = TeamMembership.objects.get(user=parent)
        self.assertEqual(membership.team, team)
        self.assertTrue(membership.is_active)
        parent.profile.refresh_from_db()
        self.assertEqual(parent.profile.child_name, "Maya Salem")
        parent.refresh_from_db()
        self.assertEqual(parent.first_name, "Rola")
        self.assertEqual(parent.last_name, "Itani")
        self.assertEqual(str(parent.profile.date_of_birth), "1987-09-10")
        self.assertEqual(parent.profile.position, "")
        self.assertIsNone(parent.profile.jersey_number)

        roster_response = self.client.get(reverse("team_management:roster"))
        self.assertContains(roster_response, "Rola Itani")
        self.assertContains(roster_response, "parent@example.com")
        self.assertNotContains(roster_response, "Maya Salem")

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
        self.assertContains(response, "Parent2 User")
        self.assertNotContains(response, "Lina Haddad")

    def test_manager_can_edit_parent_without_position_or_jersey(self):
        manager = self._create_user(
            email="managerparentedit@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Lions Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        parent = self._create_user(email="parentedit@example.com", role=AccountProfile.ROLE_PARENT)
        membership = TeamMembership.objects.create(
            user=parent,
            team=team,
            member_title="Parent",
            added_by=manager,
            is_active=True,
        )
        child_one = self._create_user(email="childone@example.com", role=AccountProfile.ROLE_PLAYER)
        child_two = self._create_user(email="childtwo@example.com", role=AccountProfile.ROLE_PLAYER)
        ParentChildLink.objects.create(parent_profile=parent.profile, child_profile=child_one.profile)
        ParentChildLink.objects.create(parent_profile=parent.profile, child_profile=child_two.profile)

        response = self.client.post(
            reverse("team_management:edit_member", args=[membership.pk]),
            data={
                "member_category": "player",
                "first_name": "Issam",
                "last_name": "Mourtada",
                "email": "parentedit@example.com",
                "phone_number": "+96170000001",
                "date_of_birth": "1988-12-02",
                "jersey_number": "",
                "position": "",
                "member_title": "Parent",
                "is_active": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "Member details were updated successfully.")
        parent.refresh_from_db()
        self.assertEqual(parent.first_name, "Issam")
        self.assertEqual(parent.last_name, "Mourtada")
        self.assertEqual(str(parent.profile.date_of_birth), "1988-12-02")
        self.assertIsNone(parent.profile.jersey_number)
        self.assertEqual(parent.profile.position, "")

        edit_page = self.client.get(reverse("team_management:edit_member", args=[membership.pk]))
        self.assertContains(edit_page, "Childone User")
        self.assertContains(edit_page, "Childtwo User")

    def test_manager_can_delete_member_from_roster(self):
        manager = self._create_user(
            email="manager8@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Phoenix Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player = self._create_user(email="deleteplayer@example.com", role=AccountProfile.ROLE_PLAYER)
        membership = TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)

        response = self.client.post(
            reverse("team_management:delete_member", args=[membership.pk]),
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertFalse(TeamMembership.objects.filter(pk=membership.pk).exists())
        self.assertContains(response, "Player deleted.")

    def test_deleting_parent_from_roster_also_removes_linked_children(self):
        manager = self._create_user(
            email="managerdeleteparent@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Harbor Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        parent = self._create_user(email="parentdelete@example.com", role=AccountProfile.ROLE_PARENT)
        child_one = self._create_user(email="childdelete1@example.com", role=AccountProfile.ROLE_PLAYER)
        child_two = self._create_user(email="childdelete2@example.com", role=AccountProfile.ROLE_PLAYER)

        parent_membership = TeamMembership.objects.create(user=parent, team=team, member_title="Parent", added_by=manager)
        child_one_membership = TeamMembership.objects.create(user=child_one, team=team, member_title="Player", added_by=manager)
        child_two_membership = TeamMembership.objects.create(user=child_two, team=team, member_title="Player", added_by=manager)

        ParentChildLink.objects.create(parent_profile=parent.profile, child_profile=child_one.profile)
        ParentChildLink.objects.create(parent_profile=parent.profile, child_profile=child_two.profile)
        parent.profile.linked_player = child_one.profile
        parent.profile.child_name = child_one.get_full_name()
        parent.profile.save(update_fields=["linked_player", "child_name"])

        response = self.client.post(
            reverse("team_management:delete_member", args=[parent_membership.pk]),
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "linked child roster entries")
        self.assertFalse(TeamMembership.objects.filter(pk=parent_membership.pk).exists())
        self.assertFalse(TeamMembership.objects.filter(pk=child_one_membership.pk).exists())
        self.assertFalse(TeamMembership.objects.filter(pk=child_two_membership.pk).exists())

    def test_deleting_already_removed_member_redirects_to_roster(self):
        manager = self._create_user(
            email="manageralreadyremoved@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Harbor Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player = self._create_user(email="alreadyremoved@example.com", role=AccountProfile.ROLE_PLAYER)
        membership = TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)
        membership_id = membership.pk
        membership.delete()

        response = self.client.post(
            reverse("team_management:delete_member", args=[membership_id]),
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "This roster member was already removed.")

    def test_editing_removed_member_redirects_to_roster(self):
        manager = self._create_user(
            email="managereditremoved@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Harbor Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player = self._create_user(email="editremoved@example.com", role=AccountProfile.ROLE_PLAYER)
        membership = TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)
        membership_id = membership.pk
        membership.delete()

        response = self.client.get(reverse("team_management:edit_member", args=[membership_id]), follow=True)

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "This roster member was already removed.")

    def test_reviewing_removed_request_redirects_to_roster(self):
        manager = self._create_user(
            email="managerreviewremoved@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Harbor Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        pending_user = self._create_user(email="reviewremoved@example.com", role=AccountProfile.ROLE_PLAYER)
        membership = TeamMembership.objects.create(
            user=pending_user,
            team=team,
            member_title="Pending Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_PENDING,
            is_active=False,
        )
        membership_id = membership.pk
        membership.delete()

        response = self.client.post(
            reverse("team_management:review_member_request", args=[membership_id]),
            data={"action": "approve", "approved_role": AccountProfile.ROLE_PLAYER},
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "This membership request was already removed.")

    def test_rejecting_parent_request_also_removes_linked_child_requests(self):
        manager = self._create_user(
            email="managerreject@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Orbit Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        parent = self._create_user(email="parentreject@example.com", role=AccountProfile.ROLE_PARENT)
        child_one = self._create_user(email="childreject1@example.com", role=AccountProfile.ROLE_PLAYER)
        child_two = self._create_user(email="childreject2@example.com", role=AccountProfile.ROLE_PLAYER)

        parent_membership = TeamMembership.objects.create(
            user=parent,
            team=team,
            member_title="Pending Parent",
            requested_role=AccountProfile.ROLE_PARENT,
            status=TeamMembership.STATUS_PENDING,
            is_active=False,
        )
        TeamMembership.objects.create(
            user=child_one,
            team=team,
            member_title="Pending Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_PENDING,
            is_active=False,
        )
        TeamMembership.objects.create(
            user=child_two,
            team=team,
            member_title="Pending Player",
            requested_role=AccountProfile.ROLE_PLAYER,
            status=TeamMembership.STATUS_PENDING,
            is_active=False,
        )
        ParentChildLink.objects.create(parent_profile=parent.profile, child_profile=child_one.profile)
        ParentChildLink.objects.create(parent_profile=parent.profile, child_profile=child_two.profile)
        parent.profile.linked_player = child_one.profile
        parent.profile.child_name = child_one.get_full_name()
        parent.profile.save(update_fields=["linked_player", "child_name"])

        response = self.client.post(
            reverse("team_management:review_member_request", args=[parent_membership.pk]),
            data={"action": "reject"},
            follow=True,
        )

        self.assertRedirects(response, reverse("team_management:roster"))
        self.assertContains(response, "linked child requests")
        self.assertFalse(User.objects.filter(email="parentreject@example.com").exists())
        self.assertFalse(User.objects.filter(email="childreject1@example.com").exists())
        self.assertFalse(User.objects.filter(email="childreject2@example.com").exists())

    def test_staff_can_delete_players_but_not_managers(self):
        manager = self._create_user(
            email="manager9@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Nova Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        staff = self._create_user(email="staffdelete@example.com", role=AccountProfile.ROLE_STAFF)
        player = self._create_user(email="stafftarget@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=staff, team=team, member_title="Staff", added_by=manager)
        player_membership = TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)

        self.client.force_login(staff)
        response = self.client.get(reverse("team_management:roster"))
        members = {member["user"].email: member for member in response.context["members"]}
        self.assertTrue(members["stafftarget@example.com"]["can_delete"])
        self.assertFalse(members["manager9@example.com"]["can_delete"])

        blocked_response = self.client.post(
            reverse("team_management:delete_member", args=[TeamMembership.objects.get(user=manager).pk]),
            follow=True,
        )
        self.assertRedirects(blocked_response, reverse("team_management:roster"))
        self.assertContains(blocked_response, "Staff members cannot remove managers from the roster.")
        self.assertTrue(TeamMembership.objects.filter(user=manager, team=team).exists())

        allowed_response = self.client.post(
            reverse("team_management:delete_member", args=[player_membership.pk]),
            follow=True,
        )
        self.assertRedirects(allowed_response, reverse("team_management:roster"))
        self.assertFalse(TeamMembership.objects.filter(pk=player_membership.pk).exists())

    def test_player_cannot_access_delete_member(self):
        manager = self._create_user(
            email="manager10@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Atlas Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        player_a = self._create_user(email="playera@example.com", role=AccountProfile.ROLE_PLAYER)
        player_b = self._create_user(email="playerb@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=player_a, team=team, member_title="Player", added_by=manager)
        player_b_membership = TeamMembership.objects.create(user=player_b, team=team, member_title="Player", added_by=manager)

        self.client.force_login(player_a)
        response = self.client.get(reverse("team_management:roster"))
        self.assertNotContains(response, "Delete")

        blocked_response = self.client.post(
            reverse("team_management:delete_member", args=[player_b_membership.pk]),
            follow=True,
        )
        self.assertRedirects(blocked_response, reverse("team_management:roster"))
        self.assertContains(blocked_response, "Only managers and staff can remove roster members.")
        self.assertTrue(TeamMembership.objects.filter(pk=player_b_membership.pk).exists())

    def test_coach_cannot_access_delete_member(self):
        manager = self._create_user(
            email="manager11@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Comets Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        coach = self._create_user(email="coachdelete@example.com", role=AccountProfile.ROLE_COACH)
        player = self._create_user(email="coachtarget@example.com", role=AccountProfile.ROLE_PLAYER)
        TeamMembership.objects.create(user=coach, team=team, member_title="Coach", added_by=manager)
        player_membership = TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)

        self.client.force_login(coach)
        response = self.client.get(reverse("team_management:roster"))
        self.assertNotContains(response, "Delete")

        blocked_response = self.client.post(
            reverse("team_management:delete_member", args=[player_membership.pk]),
            follow=True,
        )
        self.assertRedirects(blocked_response, reverse("team_management:roster"))
        self.assertContains(blocked_response, "Only managers and staff can remove roster members.")
        self.assertTrue(TeamMembership.objects.filter(pk=player_membership.pk).exists())

    def test_coach_can_edit_players_but_not_staff_or_coaches(self):
        manager = self._create_user(
            email="manager12@example.com",
            role=AccountProfile.ROLE_MANAGER,
            club_name="Titans Club",
        )
        self.client.force_login(manager)
        self.client.get(reverse("team_management:roster"))
        team = TeamMembership.objects.get(user=manager).team

        coach = self._create_user(email="coachlead@example.com", role=AccountProfile.ROLE_COACH)
        player = self._create_user(email="editableplayer@example.com", role=AccountProfile.ROLE_PLAYER)
        staff = self._create_user(email="lockedstaff@example.com", role=AccountProfile.ROLE_STAFF)
        other_coach = self._create_user(email="lockedcoach@example.com", role=AccountProfile.ROLE_COACH)

        TeamMembership.objects.create(user=coach, team=team, member_title="Coach", added_by=manager)
        player_membership = TeamMembership.objects.create(user=player, team=team, member_title="Player", added_by=manager)
        staff_membership = TeamMembership.objects.create(user=staff, team=team, member_title="Staff", added_by=manager)
        coach_membership = TeamMembership.objects.create(user=other_coach, team=team, member_title="Coach", added_by=manager)

        self.client.force_login(coach)
        response = self.client.get(reverse("team_management:roster"))
        members = {member["user"].email: member for member in response.context["members"]}

        self.assertTrue(members["editableplayer@example.com"]["can_edit"])
        self.assertFalse(members["lockedstaff@example.com"]["can_edit"])
        self.assertFalse(members["lockedcoach@example.com"]["can_edit"])

        player_edit_response = self.client.get(reverse("team_management:edit_member", args=[player_membership.pk]))
        self.assertEqual(player_edit_response.status_code, 200)

        staff_edit_response = self.client.get(
            reverse("team_management:edit_member", args=[staff_membership.pk]),
            follow=True,
        )
        self.assertRedirects(staff_edit_response, reverse("team_management:roster"))
        self.assertContains(staff_edit_response, "Coaches can only edit player roster members.")

        coach_edit_response = self.client.get(
            reverse("team_management:edit_member", args=[coach_membership.pk]),
            follow=True,
        )
        self.assertRedirects(coach_edit_response, reverse("team_management:roster"))
        self.assertContains(coach_edit_response, "Coaches can only edit player roster members.")
