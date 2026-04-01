import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import AccountProfile
from performance.models import TeamPerformanceRecord
from scheduling.models import ScheduledEvent
from team_management.models import Team, TeamMembership

User = get_user_model()


def ensure_user(team, email, first_name, last_name, role):
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"username": email, "first_name": first_name, "last_name": last_name},
    )
    if created:
        user.set_password("Pass12345!")
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
    return user, membership


def run():
    team, _ = Team.objects.get_or_create(name="Lions", defaults={"created_by": None})

    coach_user, _ = ensure_user(team, "coach.lions@example.com", "Lina", "Coach", AccountProfile.ROLE_COACH)
    ensure_user(team, "manager.lions@example.com", "Mona", "Manager", AccountProfile.ROLE_MANAGER)
    ensure_user(team, "staff.lions@example.com", "Sam", "Staff", AccountProfile.ROLE_STAFF)

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
            else:
                points_scored = 0
                points_conceded = 0
                kills = 0
                aces = 0
                blocks = 0
                assists = 0
                digs = 0
                unforced_errors = 0

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

    print(
        "Seed complete:",
        "players=",
        TeamMembership.objects.filter(team=team, user__profile__role=AccountProfile.ROLE_PLAYER).count(),
        "events=",
        ScheduledEvent.objects.filter(team=team).count(),
        "records=",
        TeamPerformanceRecord.objects.filter(team=team).count(),
    )


run()
