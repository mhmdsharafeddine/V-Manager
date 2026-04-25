import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import AccountProfile, NotificationPreferences, ParentChildLink
from communication.models import (
    Announcement,
    AnnouncementComment,
    AnnouncementMessage,
    AnnouncementMessageRead,
    AnnouncementRecipient,
    PrivateMessage,
)
from performance.models import TeamPerformanceRecord
from scheduling.models import ScheduledEvent
from team_management.models import Team, TeamMembership

User = get_user_model()
DEFAULT_PASSWORD = "Pass12345!"
ADMIN_PASSWORD = "AdminPass123!"


def ensure_user(team, email, first_name, last_name, role):
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"username": email, "first_name": first_name, "last_name": last_name},
    )
    if created:
        user.set_password(DEFAULT_PASSWORD)
        user.save(update_fields=["password"])

    profile, _ = AccountProfile.objects.get_or_create(user=user, defaults={"role": role})
    profile.role = role
    profile.save(update_fields=["role"])

    membership, _ = TeamMembership.objects.get_or_create(
        user=user,
        defaults={
            "team": team,
            "member_title": role.title(),
            "status": TeamMembership.STATUS_APPROVED,
            "is_active": True,
        },
    )
    membership.team = team
    membership.status = TeamMembership.STATUS_APPROVED
    membership.is_active = True
    membership.save(update_fields=["team", "status", "is_active"])

    NotificationPreferences.for_user(user)
    return user, membership


def run():
    random.seed(430)

    seeded_accounts = []

    admin_user, created = User.objects.get_or_create(
        username="admin@example.com",
        defaults={
            "email": "admin@example.com",
            "first_name": "System",
            "last_name": "Admin",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    if created:
        admin_user.set_password(ADMIN_PASSWORD)
        admin_user.save(update_fields=["password"])
    else:
        changed = False
        if not admin_user.is_staff:
            admin_user.is_staff = True
            changed = True
        if not admin_user.is_superuser:
            admin_user.is_superuser = True
            changed = True
        if changed:
            admin_user.save(update_fields=["is_staff", "is_superuser"])
    seeded_accounts.append((admin_user.email, ADMIN_PASSWORD, "admin"))

    team, _ = Team.objects.get_or_create(name="Lions", defaults={"created_by": None})

    coach_user, _ = ensure_user(team, "coach.lions@example.com", "Lina", "Coach", AccountProfile.ROLE_COACH)
    manager_user, _ = ensure_user(team, "manager.lions@example.com", "Mona", "Manager", AccountProfile.ROLE_MANAGER)
    staff_user, _ = ensure_user(team, "staff.lions@example.com", "Sam", "Staff", AccountProfile.ROLE_STAFF)

    seeded_accounts.extend(
        [
            (coach_user.email, DEFAULT_PASSWORD, "coach"),
            (manager_user.email, DEFAULT_PASSWORD, "manager"),
            (staff_user.email, DEFAULT_PASSWORD, "staff"),
        ]
    )

    players = []
    player_names = [
        ("Hassan", "Yuta"),
        ("Fadi", "Al Dowri"),
        ("Tala", "Mansadar"),
        ("Mohamed", "Ransis"),
        ("Hussein", "Rashid"),
        ("Karim", "Nader"),
        ("Nour", "Salem"),
        ("Yara", "Haddad"),
    ]
    for i, (first, last) in enumerate(player_names, start=1):
        user, membership = ensure_user(team, f"player{i}.lions@example.com", first, last, AccountProfile.ROLE_PLAYER)
        user.first_name = first
        user.last_name = last
        user.save(update_fields=["first_name", "last_name"])

        profile = user.profile
        profile.position = random.choice(["Setter", "Libero", "Outside Hitter", "Middle Blocker", "Opposite Hitter"])
        profile.jersey_number = 10 + i
        profile.save(update_fields=["position", "jersey_number"])

        membership.member_title = "Player"
        membership.save(update_fields=["member_title"])
        players.append((user, membership))
        seeded_accounts.append((user.email, DEFAULT_PASSWORD, "player"))

    parent_user, _ = ensure_user(
        team,
        "parent.lions@example.com",
        "Nadia",
        "Parent",
        AccountProfile.ROLE_PARENT,
    )
    parent_profile = parent_user.profile
    primary_child_profile = players[0][0].profile
    parent_profile.child_name = primary_child_profile.user.get_full_name().strip()
    parent_profile.linked_player = primary_child_profile
    parent_profile.save(update_fields=["child_name", "linked_player"])
    ParentChildLink.objects.get_or_create(
        parent_profile=parent_profile,
        child_profile=primary_child_profile,
    )
    seeded_accounts.append((parent_user.email, DEFAULT_PASSWORD, "parent"))

    base = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(days=60)
    event_specs = [
        ("Match Day A", ScheduledEvent.TYPE_MATCH, -40),
        ("Training Block 1", ScheduledEvent.TYPE_TRAINING, -30),
        ("Match Day B", ScheduledEvent.TYPE_MATCH, -18),
        ("Tournament Prep", ScheduledEvent.TYPE_TOURNAMENT, -7),
        ("Practice Session", ScheduledEvent.TYPE_PRACTICE, 3),
        ("Friendly Match", ScheduledEvent.TYPE_MATCH, 10),
    ]

    events = []
    for idx, (title, event_type, day_offset) in enumerate(event_specs, start=1):
        dt = base + timedelta(days=day_offset, hours=idx)
        event, _ = ScheduledEvent.objects.get_or_create(
            team=team,
            scheduled_at=dt,
            defaults={
                "created_by": coach_user,
                "title": title,
                "event_type": event_type,
                "location": "Main Court",
                "duration_minutes": 90,
                "audience": ScheduledEvent.AUDIENCE_PLAYERS,
                "status": ScheduledEvent.STATUS_SCHEDULED,
            },
        )
        events.append(event)

    for event in events:
        chosen = random.sample(players, k=min(6, len(players)))
        for user, membership in chosen:
            attendance = random.choices(
                [
                    TeamPerformanceRecord.PARTICIPATION_PRESENT,
                    TeamPerformanceRecord.PARTICIPATION_ABSENT,
                    TeamPerformanceRecord.PARTICIPATION_DID_NOT_ATTEND,
                    TeamPerformanceRecord.PARTICIPATION_INJURED,
                    TeamPerformanceRecord.PARTICIPATION_EXCUSED,
                ],
                weights=[70, 8, 8, 8, 6],
                k=1,
            )[0]

            if attendance == TeamPerformanceRecord.PARTICIPATION_PRESENT:
                points_scored = random.randint(8, 30)
                points_conceded = random.randint(5, 25)
                kills = random.randint(1, 16)
                aces = random.randint(0, 6)
                blocks = random.randint(0, 8)
                assists = random.randint(0, 12)
                digs = random.randint(0, 14)
                unforced_errors = random.randint(0, 7)
                injury_status = ""
            else:
                points_scored = 0
                points_conceded = 0
                kills = 0
                aces = 0
                blocks = 0
                assists = 0
                digs = 0
                unforced_errors = 0
                injury_status = random.choice(
                    [
                        TeamPerformanceRecord.INJURY_MINOR_ISSUE,
                        TeamPerformanceRecord.INJURY_RECOVERING,
                        TeamPerformanceRecord.INJURY_RECENTLY_INJURED,
                    ]
                ) if attendance == TeamPerformanceRecord.PARTICIPATION_INJURED else ""

            TeamPerformanceRecord.objects.update_or_create(
                event=event,
                member=membership,
                defaults={
                    "team": team,
                    "recorded_by": coach_user,
                    "participation_status": attendance,
                    "result": random.choice(
                        [
                            TeamPerformanceRecord.RESULT_WIN,
                            TeamPerformanceRecord.RESULT_LOSS,
                            TeamPerformanceRecord.RESULT_DRAW,
                        ]
                    ),
                    "injury_status": injury_status,
                    "points_scored": points_scored,
                    "points_conceded": points_conceded,
                    "target_score": 20,
                    "target_achieved": random.choice([True, False]) if attendance == TeamPerformanceRecord.PARTICIPATION_PRESENT else False,
                    "kills": kills,
                    "aces": aces,
                    "blocks": blocks,
                    "assists": assists,
                    "digs": digs,
                    "unforced_errors": unforced_errors,
                    "notes": "Auto-seeded sample data",
                },
            )

    announcement_specs = [
        ("Weekly Training Plan", "Updated schedule is now available in the team area.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_INFO, True),
        ("Match Kit Reminder", "Bring your complete match kit by 6:30 PM.", Announcement.AUDIENCE_PLAYERS, Announcement.PRIORITY_IMPORTANT, False),
        ("Urgent Venue Change", "Saturday match moved to Court B due to maintenance.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_URGENT, True),
    ]

    for idx, (title, body, audience, priority, pin_to_top) in enumerate(announcement_specs, start=1):
        announcement, _ = Announcement.objects.get_or_create(
            team=team,
            created_by=coach_user,
            title=title,
            defaults={
                "body": body,
                "audience": audience,
                "priority": priority,
                "send_push_notification": True,
                "send_email_notification": True,
                "send_sms_notification": False,
                "pin_to_top": pin_to_top,
            },
        )

        recipients = TeamMembership.objects.filter(
            team=team,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
        ).select_related("user")

        for membership in recipients:
            recipient, _ = AnnouncementRecipient.objects.get_or_create(
                announcement=announcement,
                user=membership.user,
            )
            if membership.user == coach_user:
                recipient.read_at = timezone.now() - timedelta(minutes=idx)
                recipient.save(update_fields=["read_at"])

        message, _ = AnnouncementMessage.objects.get_or_create(
            announcement=announcement,
            author=coach_user,
            body=f"Coach follow-up note #{idx}",
        )
        AnnouncementMessageRead.objects.get_or_create(message=message, user=coach_user)

        comment, _ = AnnouncementComment.objects.get_or_create(
            announcement=announcement,
            author=manager_user,
            parent=None,
            body=f"Manager acknowledgement for announcement #{idx}",
        )
        AnnouncementComment.objects.get_or_create(
            announcement=announcement,
            author=staff_user,
            parent=comment,
            body=f"Staff follow-up for announcement #{idx}",
        )

    dm_pairs = [
        (coach_user, players[0][0], "Great effort in today\'s drills."),
        (players[0][0], coach_user, "Thanks coach, I will review the feedback."),
        (manager_user, parent_user, "Please confirm transport details for Saturday."),
    ]
    for sender, recipient, body in dm_pairs:
        PrivateMessage.objects.get_or_create(
            sender=sender,
            recipient=recipient,
            team=team,
            body=body,
        )

    print(
        "Seed complete:",
        "players=",
        TeamMembership.objects.filter(team=team, user__profile__role=AccountProfile.ROLE_PLAYER).count(),
        "events=",
        ScheduledEvent.objects.filter(team=team).count(),
        "records=",
        TeamPerformanceRecord.objects.filter(team=team).count(),
        "announcements=",
        Announcement.objects.filter(team=team).count(),
        "private_messages=",
        PrivateMessage.objects.filter(team=team).count(),
    )

    print("\nSeeded credentials:")
    for email, password, role in seeded_accounts:
        print(f"- {email} | {password} | {role}")


run()
