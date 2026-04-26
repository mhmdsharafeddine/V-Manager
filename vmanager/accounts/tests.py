from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core import mail

from .jwt_utils import create_access_token
from .models import AccountProfile, EmailVerificationCode
from team_management.models import Team, TeamMembership

User = get_user_model()


class AccountProfileTests(TestCase):
    def test_register_view_creates_hashed_user_and_profile(self):
        response = self.client.post(
            reverse("accounts:register"),
            data={
                "signup_type": "manager",
                "first_name": "Maya",
                "last_name": "Saab",
                "date_of_birth": "1990-03-12",
                "email": "maya@example.com",
                "password": "StrongPass123!",
                "team_name": "Meis Volleyball Club",
            },
        )

        self.assertRedirects(response, reverse("accounts:login"))
        self.assertEqual(len(mail.outbox), 0)
        user = User.objects.get(email="maya@example.com")
        self.assertNotEqual(user.password, "StrongPass123!")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertEqual(user.first_name, "Maya")
        self.assertEqual(user.last_name, "Saab")
        self.assertEqual(user.profile.club_name, "Meis Volleyball Club")
        self.assertEqual(user.profile.role, AccountProfile.ROLE_MANAGER)
        self.assertEqual(str(user.profile.date_of_birth), "1990-03-12")
        self.assertEqual(user.team_membership.status, TeamMembership.STATUS_APPROVED)

    def test_public_member_registration_is_blocked(self):
        manager_user = User.objects.create_user(
            username="manager@example.com",
            email="manager@example.com",
            password="StrongPass123!",
        )
        AccountProfile.objects.create(user=manager_user, role=AccountProfile.ROLE_MANAGER, club_name="Lions")
        team = Team.objects.create(name="Lions", created_by=manager_user)

        response = self.client.post(
            reverse("accounts:register"),
            data={
                "signup_type": "member",
                "first_name": "Nour",
                "last_name": "Haddad",
                "date_of_birth": "2005-01-04",
                "email": "nour@example.com",
                "password": "StrongPass123!",
                "team": str(team.id),
                "requested_role": AccountProfile.ROLE_PLAYER,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public registration is only available for team managers.")
        self.assertFalse(User.objects.filter(email="nour@example.com").exists())

    def test_register_with_existing_username_shows_form_error_instead_of_crashing(self):
        User.objects.create_user(
            username="maya@example.com",
            email="different@example.com",
            password="StrongPass123!",
        )

        response = self.client.post(
            reverse("accounts:register"),
            data={
                "signup_type": "manager",
                "first_name": "Maya",
                "last_name": "Saab",
                "date_of_birth": "1990-03-12",
                "email": "maya@example.com",
                "password": "StrongPass123!",
                "team_name": "Meis Volleyball Club",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists.")
        self.assertEqual(User.objects.filter(username="maya@example.com").count(), 1)

    def test_dashboard_allows_valid_jwt_cookie(self):
        user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="StrongPass123!",
            first_name="Coach",
        )
        AccountProfile.objects.create(
            user=user,
            role=AccountProfile.ROLE_COACH,
            club_name="V-Manager Club",
        )

        access_token = create_access_token(user)
        self.client.cookies["vm_access_token"] = access_token

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "V-Manager Club")

    def test_settings_requires_authentication(self):
        response = self.client.get(reverse("accounts:settings"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_settings_updates_profile_details_and_password(self):
        user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="StrongPass123!",
            first_name="Old",
            last_name="Name",
        )
        profile = AccountProfile.objects.create(
            user=user,
            role=AccountProfile.ROLE_COACH,
            club_name="Lions",
            phone_number="+961111111",
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:settings"),
            data={
                "first_name": "Maya",
                "last_name": "Saab",
                "email": "maya@example.com",
                "phone_number": "+96170000000",
                "date_of_birth": "1994-03-12",
                "remove_profile_photo": "",
                "current_password": "StrongPass123!",
                "new_password1": "NewStrongPass123!",
                "new_password2": "NewStrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("accounts:settings"))
        user.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(user.first_name, "Maya")
        self.assertEqual(user.last_name, "Saab")
        self.assertEqual(user.email, "maya@example.com")
        self.assertEqual(user.username, "maya@example.com")
        self.assertEqual(profile.phone_number, "+96170000000")
        self.assertEqual(str(profile.date_of_birth), "1994-03-12")
        self.assertTrue(user.check_password("NewStrongPass123!"))

    def test_login_without_remember_me_sets_session_cookies(self):
        user = User.objects.create_user(
            username="session@example.com",
            email="session@example.com",
            password="StrongPass123!",
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.ROLE_COACH)

        response = self.client.post(
            reverse("accounts:login"),
            data={
                "email": "session@example.com",
                "password": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("accounts:verify_2fa"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailVerificationCode.objects.filter(user=user, used_at__isnull=True).count(), 1)

    def test_login_with_remember_me_sets_persistent_cookies(self):
        user = User.objects.create_user(
            username="remember@example.com",
            email="remember@example.com",
            password="StrongPass123!",
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.ROLE_COACH)

        response = self.client.post(
            reverse("accounts:login"),
            data={
                "email": "remember@example.com",
                "password": "StrongPass123!",
                "remember_me": "on",
            },
        )

        self.assertRedirects(response, reverse("accounts:verify_2fa"))
        self.assertTrue(self.client.session["pending_auth_remember_me"])

    def test_verify_2fa_logs_user_in_and_sets_cookies(self):
        user = User.objects.create_user(
            username="otp@example.com",
            email="otp@example.com",
            password="StrongPass123!",
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.ROLE_COACH)
        session = self.client.session
        session["pending_auth_user_id"] = user.pk
        session["pending_auth_remember_me"] = True
        session.save()

        verification, code = EmailVerificationCode.create_code(
            user,
            EmailVerificationCode.PURPOSE_LOGIN_2FA,
        )

        response = self.client.post(reverse("accounts:verify_2fa"), data={"code": code})

        self.assertRedirects(response, reverse("home"))
        verification.refresh_from_db()
        self.assertIsNotNone(verification.used_at)
        self.assertIn("vm_access_token", response.cookies)

    def test_verify_2fa_redirects_invited_user_to_initial_password_change(self):
        user = User.objects.create_user(
            username="invited@example.com",
            email="invited@example.com",
            password="TempPass123!",
        )
        AccountProfile.objects.create(
            user=user,
            role=AccountProfile.ROLE_PLAYER,
            must_change_password=True,
        )
        session = self.client.session
        session["pending_auth_user_id"] = user.pk
        session["pending_auth_remember_me"] = False
        session.save()

        verification, code = EmailVerificationCode.create_code(
            user,
            EmailVerificationCode.PURPOSE_LOGIN_2FA,
        )

        response = self.client.post(reverse("accounts:verify_2fa"), data={"code": code})

        self.assertRedirects(response, reverse("accounts:initial_password_change"))
        verification.refresh_from_db()
        self.assertIsNotNone(verification.used_at)

    def test_initial_password_change_updates_password_and_clears_flag(self):
        user = User.objects.create_user(
            username="firstlogin@example.com",
            email="firstlogin@example.com",
            password="TempPass123!",
        )
        AccountProfile.objects.create(
            user=user,
            role=AccountProfile.ROLE_COACH,
            must_change_password=True,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("accounts:initial_password_change"),
            data={
                "new_password1": "NewStrongPass123!",
                "new_password2": "NewStrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("home"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewStrongPass123!"))
        self.assertFalse(user.profile.must_change_password)

    def test_forgot_password_sends_email(self):
        user = User.objects.create_user(
            username="reset@example.com",
            email="reset@example.com",
            password="StrongPass123!",
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.ROLE_COACH)

        response = self.client.post(
            reverse("accounts:forgot_password"),
            data={"email": "reset@example.com"},
        )

        self.assertRedirects(response, reverse("accounts:password_reset_sent"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password reset code", mail.outbox[0].subject.lower())
        self.assertTrue(self.client.session["pending_password_reset_user_id"], user.pk)

    def test_verify_reset_code_opens_password_reset_form(self):
        user = User.objects.create_user(
            username="resetcode@example.com",
            email="resetcode@example.com",
            password="OldPass123!",
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.ROLE_COACH)

        response = self.client.post(
            reverse("accounts:forgot_password"),
            data={"email": "resetcode@example.com"},
        )

        self.assertRedirects(response, reverse("accounts:password_reset_sent"))
        self.assertEqual(len(mail.outbox), 1)

        body = mail.outbox[0].body
        reset_code = next(line.strip() for line in body.splitlines() if line.strip().isdigit() and len(line.strip()) == 6)

        verify_response = self.client.post(
            reverse("accounts:verify_password_reset_code"),
            data={"code": reset_code},
        )

        self.assertRedirects(verify_response, reverse("accounts:password_reset_confirm"))
        reset_form_response = self.client.get(reverse("accounts:password_reset_confirm"))
        self.assertEqual(reset_form_response.status_code, 200)
        self.assertContains(reset_form_response, "Create a New Password")

    def test_password_reset_confirm_updates_password(self):
        user = User.objects.create_user(
            username="confirm@example.com",
            email="confirm@example.com",
            password="OldPass123!",
        )
        AccountProfile.objects.create(user=user, role=AccountProfile.ROLE_COACH, must_change_password=True)
        session = self.client.session
        session["verified_password_reset_user_id"] = user.pk
        session.save()

        response = self.client.post(
            reverse("accounts:password_reset_confirm"),
            data={
                "new_password1": "NewStrongPass123!",
                "new_password2": "NewStrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewStrongPass123!"))
        self.assertFalse(user.profile.must_change_password)
