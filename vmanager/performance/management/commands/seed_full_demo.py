import random
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import AccountProfile, NotificationPreferences, ParentChildLink
from communication.models import (
    Announcement,
    AnnouncementComment,
    AnnouncementMessage,
    AnnouncementMessageRead,
    AnnouncementRecipient,
    PrivateMessage,
    UserPresence,
)
from performance.models import TeamPerformanceRecord
from scheduling.models import EventAttendance, EventNotificationRead, ScheduledEvent
from team_management.models import Team, TeamMembership

User = get_user_model()

DEFAULT_PASSWORD = "Pass12345!"
ADMIN_PASSWORD = "AdminPass123!"


def _aware(year, month, day, hour=18, minute=0):
    return timezone.make_aware(
        datetime(year, month, day, hour, minute),
        timezone.get_current_timezone(),
    )


def _season_start_year(now_local):
    return now_local.year if now_local.month >= 9 else now_local.year - 1


def _announcement_target_role(audience):
    return {
        Announcement.AUDIENCE_PLAYERS: AccountProfile.ROLE_PLAYER,
        Announcement.AUDIENCE_COACHES: AccountProfile.ROLE_COACH,
        Announcement.AUDIENCE_STAFF: AccountProfile.ROLE_STAFF,
        Announcement.AUDIENCE_PARENTS: AccountProfile.ROLE_PARENT,
        Announcement.AUDIENCE_MANAGERS: AccountProfile.ROLE_MANAGER,
    }.get(audience)


def _ensure_user(
    *,
    team,
    email,
    first_name,
    last_name,
    role,
    password,
    member_title,
    status=TeamMembership.STATUS_APPROVED,
    is_active=True,
    position="",
    jersey_number=None,
    phone_number="",
    date_of_birth=None,
):
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "username": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    changed_user_fields = []
    if created or not user.check_password(password):
        user.set_password(password)
        changed_user_fields.append("password")
    if user.username != email:
        user.username = email
        changed_user_fields.append("username")
    if user.first_name != first_name:
        user.first_name = first_name
        changed_user_fields.append("first_name")
    if user.last_name != last_name:
        user.last_name = last_name
        changed_user_fields.append("last_name")
    if changed_user_fields:
        user.save()

    profile, _ = AccountProfile.objects.get_or_create(user=user, defaults={"role": role})
    profile.role = role
    profile.club_name = team.name
    profile.position = position
    profile.jersey_number = jersey_number
    profile.phone_number = phone_number
    profile.date_of_birth = date_of_birth
    profile.save()

    membership, _ = TeamMembership.objects.get_or_create(
        user=user,
        defaults={
            "team": team,
            "member_title": member_title,
            "requested_role": role,
            "status": status,
            "is_active": is_active,
            "added_by": team.created_by,
            "reviewed_by": team.created_by,
            "reviewed_at": timezone.now(),
        },
    )
    membership.team = team
    membership.member_title = member_title
    membership.requested_role = role
    membership.status = status
    membership.is_active = is_active
    if membership.reviewed_by_id is None and team.created_by_id:
        membership.reviewed_by = team.created_by
    if membership.added_by_id is None and team.created_by_id:
        membership.added_by = team.created_by
    if membership.reviewed_at is None and status == TeamMembership.STATUS_APPROVED:
        membership.reviewed_at = timezone.now()
    membership.save()

    NotificationPreferences.for_user(user)
    return user, membership


def _ensure_event(*, team, created_by, title, event_type, scheduled_at, location, audience, duration_minutes, details, attendees_count):
    event, _ = ScheduledEvent.objects.get_or_create(
        team=team,
        scheduled_at=scheduled_at,
        defaults={
            "created_by": created_by,
            "title": title,
            "event_type": event_type,
            "location": location,
            "audience": audience,
            "duration_minutes": duration_minutes,
            "details": details,
            "attendees_count": attendees_count,
            "status": ScheduledEvent.STATUS_SCHEDULED,
        },
    )
    event.created_by = created_by
    event.title = title
    event.event_type = event_type
    event.location = location
    event.audience = audience
    event.duration_minutes = duration_minutes
    event.details = details
    event.attendees_count = attendees_count
    event.status = ScheduledEvent.STATUS_SCHEDULED
    event.save()
    return event


def _seed_secondary_club(
    *,
    team_name,
    slug,
    created_by,
    season_start,
    now_local,
    manager_spec,
    coach_spec,
    staff_spec,
    parent_spec,
    player_specs,
):
    random.seed(430 + sum(ord(ch) for ch in slug))

    team, _ = Team.objects.get_or_create(
        name=team_name,
        defaults={"created_by": created_by},
    )
    if team.created_by_id is None:
        team.created_by = created_by
        team.save(update_fields=["created_by"])

    manager_user, _ = _ensure_user(
        team=team,
        email=manager_spec["email"],
        first_name=manager_spec["first_name"],
        last_name=manager_spec["last_name"],
        role=AccountProfile.ROLE_MANAGER,
        password=DEFAULT_PASSWORD,
        member_title="Team Manager",
        phone_number=manager_spec["phone"],
        date_of_birth=manager_spec["dob"],
    )
    if team.created_by_id != manager_user.id:
        team.created_by = manager_user
        team.save(update_fields=["created_by"])

    coach_user, _ = _ensure_user(
        team=team,
        email=coach_spec["email"],
        first_name=coach_spec["first_name"],
        last_name=coach_spec["last_name"],
        role=AccountProfile.ROLE_COACH,
        password=DEFAULT_PASSWORD,
        member_title="Head Coach",
        phone_number=coach_spec["phone"],
        date_of_birth=coach_spec["dob"],
    )

    staff_user, _ = _ensure_user(
        team=team,
        email=staff_spec["email"],
        first_name=staff_spec["first_name"],
        last_name=staff_spec["last_name"],
        role=AccountProfile.ROLE_STAFF,
        password=DEFAULT_PASSWORD,
        member_title="Operations Staff",
        phone_number=staff_spec["phone"],
        date_of_birth=staff_spec["dob"],
    )

    players = []
    for index, spec in enumerate(player_specs, start=1):
        player_user, player_membership = _ensure_user(
            team=team,
            email=spec["email"],
            first_name=spec["first_name"],
            last_name=spec["last_name"],
            role=AccountProfile.ROLE_PLAYER,
            password=DEFAULT_PASSWORD,
            member_title="Captain" if spec.get("captain") else "Player",
            position=spec["position"],
            jersey_number=spec["jersey"],
            phone_number=spec["phone"],
            date_of_birth=spec["dob"],
        )
        players.append((player_user, player_membership))

    parent_user, _ = _ensure_user(
        team=team,
        email=parent_spec["email"],
        first_name=parent_spec["first_name"],
        last_name=parent_spec["last_name"],
        role=AccountProfile.ROLE_PARENT,
        password=DEFAULT_PASSWORD,
        member_title="Parent",
        phone_number=parent_spec["phone"],
        date_of_birth=parent_spec["dob"],
    )
    parent_profile = parent_user.profile
    parent_profile.child_name = players[0][0].get_full_name().strip()
    parent_profile.linked_player = players[0][0].profile
    parent_profile.save(update_fields=["child_name", "linked_player"])
    ParentChildLink.objects.get_or_create(
        parent_profile=parent_profile,
        child_profile=players[0][0].profile,
    )

    for user, values in [
        (manager_user, {"announcement_digest": NotificationPreferences.DIGEST_INSTANT}),
        (coach_user, {"announcement_digest": NotificationPreferences.DIGEST_INSTANT}),
        (parent_user, {"quiet_hours_enabled": True, "quiet_skip_entirely": False}),
    ]:
        prefs = NotificationPreferences.for_user(user)
        for field, value in values.items():
            setattr(prefs, field, value)
        prefs.save()

    total_headcount = TeamMembership.objects.filter(
        team=team,
        status=TeamMembership.STATUS_APPROVED,
        is_active=True,
    ).count()

    historical_specs = [
        (f"{team_name} Season Opener", ScheduledEvent.TYPE_MATCH, _aware(season_start, 9, 18, 19, 0), f"{team_name} Arena", ScheduledEvent.AUDIENCE_ALL, 110),
        ("Reception Stability Practice", ScheduledEvent.TYPE_PRACTICE, _aware(season_start, 10, 4, 18, 30), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 90),
        ("Serve Target Lab", ScheduledEvent.TYPE_TRAINING, _aware(season_start, 10, 21, 18, 0), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 85),
        ("Road League Match", ScheduledEvent.TYPE_MATCH, _aware(season_start, 11, 9, 19, 30), f"{team_name} Sports Hall", ScheduledEvent.AUDIENCE_ALL, 120),
        ("Systems Review", ScheduledEvent.TYPE_PRACTICE, _aware(season_start, 11, 27, 18, 30), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 95),
        ("Midseason Tournament Pool", ScheduledEvent.TYPE_TOURNAMENT, _aware(season_start, 12, 14, 17, 0), f"{team_name} Dome", ScheduledEvent.AUDIENCE_ALL, 130),
        ("January Tactical Reset", ScheduledEvent.TYPE_TRAINING, _aware(season_start + 1, 1, 11, 18, 15), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 88),
        ("February Form Match", ScheduledEvent.TYPE_MATCH, _aware(season_start + 1, 2, 16, 20, 0), f"{team_name} Arena", ScheduledEvent.AUDIENCE_ALL, 118),
        ("Late March Scrimmage", ScheduledEvent.TYPE_MATCH, _aware(season_start + 1, 3, 20, 19, 0), f"{team_name} Court", ScheduledEvent.AUDIENCE_ALL, 105),
        ("Recovery and Video Review", ScheduledEvent.TYPE_OTHER, now_local - timedelta(days=6, hours=2), f"{team_name} Analysis Room", ScheduledEvent.AUDIENCE_ALL, 60),
        ("Pre-Match Tactical Practice", ScheduledEvent.TYPE_TRAINING, now_local - timedelta(days=3, hours=2), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 92),
        ("Recent Competitive Match", ScheduledEvent.TYPE_MATCH, now_local - timedelta(days=1, hours=5), f"{team_name} Arena", ScheduledEvent.AUDIENCE_ALL, 115),
    ]

    future_specs = [
        ("Light Recovery Block", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=2, hours=2), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 75),
        ("Staff and Coach Sync", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=4, hours=2), f"{team_name} Meeting Room", ScheduledEvent.AUDIENCE_STAFF, 50),
        ("Upcoming Main Match", ScheduledEvent.TYPE_MATCH, now_local + timedelta(days=8, hours=4), f"{team_name} Arena", ScheduledEvent.AUDIENCE_ALL, 120),
        ("Parent Info Night", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=11, hours=1), f"{team_name} Lounge", ScheduledEvent.AUDIENCE_PARENTS, 45),
        ("Mini Tournament Day", ScheduledEvent.TYPE_TOURNAMENT, now_local + timedelta(days=15, hours=4), f"{team_name} Sports Dome", ScheduledEvent.AUDIENCE_ALL, 145),
        ("Championship Prep Practice", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=19, hours=2), f"{team_name} Court", ScheduledEvent.AUDIENCE_PLAYERS, 95),
    ]

    events = []
    for title, event_type, scheduled_at, location, audience, duration in historical_specs + future_specs:
        events.append(
            _ensure_event(
                team=team,
                created_by=coach_user if event_type != ScheduledEvent.TYPE_OTHER else manager_user,
                title=title,
                event_type=event_type,
                scheduled_at=scheduled_at,
                location=location,
                audience=audience,
                duration_minutes=duration,
                details=f"Demo event for {team_name}: {title}.",
                attendees_count=total_headcount,
            )
        )

    historical_events = [event for event in events if event.scheduled_at <= timezone.now()]
    future_events = [event for event in events if event.scheduled_at > timezone.now()]

    for event_index, event in enumerate(events):
        for player_index, (player_user, player_membership) in enumerate(players):
            selector = (event_index * 5 + player_index) % 9
            if selector in {0, 6}:
                status = EventAttendance.STATUS_NOT_ATTENDING
                reason = EventAttendance.REASON_INJURED if selector == 6 else EventAttendance.REASON_OTHER
                note = "Managed recovery" if reason == EventAttendance.REASON_INJURED else "Personal conflict"
            elif selector in {3}:
                status = EventAttendance.STATUS_MAYBE
                reason = ""
                note = ""
            else:
                status = EventAttendance.STATUS_ATTENDING
                reason = ""
                note = ""

            EventAttendance.objects.update_or_create(
                event=event,
                player=player_user,
                defaults={
                    "status": status,
                    "not_attending_reason": reason,
                    "not_attending_reason_note": note,
                },
            )

            if event in historical_events:
                participation = TeamPerformanceRecord.PARTICIPATION_PRESENT
                injury_status = ""
                if status == EventAttendance.STATUS_NOT_ATTENDING:
                    if reason == EventAttendance.REASON_INJURED:
                        participation = TeamPerformanceRecord.PARTICIPATION_INJURED
                        injury_status = random.choice(
                            [
                                TeamPerformanceRecord.INJURY_MINOR_ISSUE,
                                TeamPerformanceRecord.INJURY_RECOVERING,
                                TeamPerformanceRecord.INJURY_RECENTLY_INJURED,
                            ]
                        )
                    else:
                        participation = TeamPerformanceRecord.PARTICIPATION_DID_NOT_ATTEND
                elif status == EventAttendance.STATUS_MAYBE:
                    participation = TeamPerformanceRecord.PARTICIPATION_ABSENT

                position = player_user.profile.position.lower()
                if participation == TeamPerformanceRecord.PARTICIPATION_PRESENT:
                    attacker_bonus = 4 if "hitter" in position else 2
                    blocker_bonus = 4 if "middle blocker" in position else 1
                    setter_bonus = 5 if "setter" in position else 1
                    libero_bonus = 6 if "libero" in position else 1
                    kills = 5 + attacker_bonus + (event_index + player_index) % 6
                    aces = 1 + (player_index + event_index) % 3
                    blocks = blocker_bonus + (event_index % 3)
                    assists = setter_bonus + (event_index + player_index) % 4
                    digs = libero_bonus + (player_index % 4)
                    errors = 1 + ((event_index * 2 + player_index) % 4)
                    points_scored = 17 + (event_index % 8) + (player_index % 4)
                    points_conceded = 12 + ((event_index + player_index) % 8)
                else:
                    kills = aces = blocks = assists = digs = errors = points_scored = points_conceded = 0

                result = (
                    [TeamPerformanceRecord.RESULT_WIN, TeamPerformanceRecord.RESULT_WIN, TeamPerformanceRecord.RESULT_LOSS, TeamPerformanceRecord.RESULT_DRAW][event_index % 4]
                    if event.event_type in {ScheduledEvent.TYPE_MATCH, ScheduledEvent.TYPE_TOURNAMENT}
                    else TeamPerformanceRecord.RESULT_WIN
                )

                TeamPerformanceRecord.objects.update_or_create(
                    event=event,
                    member=player_membership,
                    defaults={
                        "team": team,
                        "recorded_by": coach_user,
                        "result": result,
                        "participation_status": participation,
                        "injury_status": injury_status,
                        "points_scored": points_scored,
                        "points_conceded": points_conceded,
                        "target_score": 18,
                        "target_achieved": participation == TeamPerformanceRecord.PARTICIPATION_PRESENT and points_scored >= 18,
                        "kills": kills,
                        "aces": aces,
                        "blocks": blocks,
                        "assists": assists,
                        "digs": digs,
                        "unforced_errors": errors,
                        "notes": f"Seeded performance data for {team_name} - {event.title}.",
                    },
                )

    for event in future_events[:2]:
        EventNotificationRead.objects.update_or_create(
            user=manager_user,
            event=event,
            defaults={"is_deleted": False},
        )

    announcement_specs = [
        (f"{team_name} Weekly Focus", f"{team_name} will emphasize first-ball control and transition discipline this week.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_INFO, True, now_local - timedelta(days=5)),
        (f"{team_name} Match Travel Reminder", "Confirm arrival times and uniforms before the next away fixture.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_IMPORTANT, False, now_local - timedelta(days=2)),
        (f"{team_name} Parent Coordination", "Please review pickup timing and return plans after the upcoming event.", Announcement.AUDIENCE_PARENTS, Announcement.PRIORITY_INFO, False, now_local - timedelta(days=1, hours=6)),
        (f"{team_name} Medical Notes", "Staff should update player recovery status before the next training session.", Announcement.AUDIENCE_STAFF, Announcement.PRIORITY_IMPORTANT, False, now_local - timedelta(hours=12)),
    ]

    active_memberships = list(
        TeamMembership.objects.filter(
            team=team,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
        ).select_related("user__profile")
    )

    for index, (title, body, audience, priority, pin_to_top, created_at) in enumerate(announcement_specs, start=1):
        created_by_user = coach_user if audience != Announcement.AUDIENCE_MANAGERS else manager_user
        announcement, _ = Announcement.objects.get_or_create(
            team=team,
            created_by=created_by_user,
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
        announcement.body = body
        announcement.audience = audience
        announcement.priority = priority
        announcement.send_push_notification = True
        announcement.send_email_notification = True
        announcement.send_sms_notification = False
        announcement.pin_to_top = pin_to_top
        announcement.save()
        Announcement.objects.filter(pk=announcement.pk).update(created_at=created_at)
        announcement.refresh_from_db()

        target_role = _announcement_target_role(audience)
        for membership in active_memberships:
            if target_role and membership.user.profile.role != target_role and membership.user_id != announcement.created_by_id:
                continue
            recipient, _ = AnnouncementRecipient.objects.get_or_create(
                announcement=announcement,
                user=membership.user,
            )
            recipient.is_deleted = False
            recipient.read_at = created_at + timedelta(hours=2) if (membership.id + index) % 2 == 0 else None
            recipient.save()

        top_comment, _ = AnnouncementComment.objects.get_or_create(
            announcement=announcement,
            author=manager_user,
            parent=None,
            body=f"{team_name} manager acknowledgement for {title}.",
        )
        AnnouncementComment.objects.get_or_create(
            announcement=announcement,
            author=players[index % len(players)][0],
            parent=top_comment,
            body=f"{team_name} player follow-up for {title}.",
        )

        message, _ = AnnouncementMessage.objects.get_or_create(
            announcement=announcement,
            author=announcement.created_by,
            body=f"Thread follow-up for {title}.",
        )
        AnnouncementMessageRead.objects.get_or_create(message=message, user=announcement.created_by)
        AnnouncementMessageRead.objects.get_or_create(message=message, user=players[index % len(players)][0])

    private_message_specs = [
        (coach_user, players[0][0], f"{team_name} film review looked strong. Keep the same tempo tomorrow.", now_local - timedelta(days=3)),
        (players[0][0], coach_user, "Will do coach. I will focus on early footwork.", now_local - timedelta(days=3, minutes=-20)),
        (manager_user, parent_user, f"{team_name} transport details are confirmed for the next match.", now_local - timedelta(days=1, hours=5)),
        (parent_user, manager_user, "Thanks, we are ready on our side.", now_local - timedelta(days=1, hours=4, minutes=20)),
        (staff_user, coach_user, "Recovery notes are updated for the next tactical session.", now_local - timedelta(hours=14)),
        (players[1][0], players[2][0], f"{team_name} serve targets looked sharp today.", now_local - timedelta(hours=6)),
    ]

    for sender, recipient, body, created_at in private_message_specs:
        message, _ = PrivateMessage.objects.get_or_create(
            sender=sender,
            recipient=recipient,
            team=team,
            body=body,
        )
        read_at = created_at + timedelta(minutes=18) if recipient in {coach_user, manager_user, staff_user} else None
        PrivateMessage.objects.filter(pk=message.pk).update(
            created_at=created_at,
            read_at=read_at,
        )

    fresh_users = [manager_user, coach_user, staff_user, players[0][0], players[1][0]]
    for offset, user in enumerate(fresh_users):
        UserPresence.objects.update_or_create(
            user=user,
            defaults={"last_seen": timezone.now() - timedelta(seconds=offset * 30)},
        )
    for offset, (user, _) in enumerate(players[2:], start=1):
        UserPresence.objects.update_or_create(
            user=user,
            defaults={"last_seen": timezone.now() - timedelta(minutes=5 + offset)},
        )
    UserPresence.objects.update_or_create(
        user=parent_user,
        defaults={"last_seen": timezone.now() - timedelta(minutes=9)},
    )


class Command(BaseCommand):
    help = "Populate the database with a rich, reusable demo dataset."

    def handle(self, *args, **options):
        random.seed(430)
        now_local = timezone.localtime(timezone.now())
        season_start = _season_start_year(now_local)

        with transaction.atomic():
            admin_user, _ = User.objects.get_or_create(
                username="admin@example.com",
                defaults={
                    "email": "admin@example.com",
                    "first_name": "System",
                    "last_name": "Admin",
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            admin_user.email = "admin@example.com"
            admin_user.first_name = "System"
            admin_user.last_name = "Admin"
            admin_user.is_staff = True
            admin_user.is_superuser = True
            admin_user.set_password(ADMIN_PASSWORD)
            admin_user.save()

            team, _ = Team.objects.get_or_create(
                name="Lions",
                defaults={"created_by": admin_user},
            )
            if team.created_by_id is None:
                team.created_by = admin_user
                team.save(update_fields=["created_by"])

            manager_user, manager_membership = _ensure_user(
                team=team,
                email="manager.lions@example.com",
                first_name="Mona",
                last_name="Manager",
                role=AccountProfile.ROLE_MANAGER,
                password=DEFAULT_PASSWORD,
                member_title="Team Manager",
                phone_number="+96170111001",
                date_of_birth=datetime(season_start - 10, 5, 12).date(),
            )
            if team.created_by_id != manager_user.id:
                team.created_by = manager_user
                team.save(update_fields=["created_by"])

            coach_user, coach_membership = _ensure_user(
                team=team,
                email="coach.lions@example.com",
                first_name="Lina",
                last_name="Coach",
                role=AccountProfile.ROLE_COACH,
                password=DEFAULT_PASSWORD,
                member_title="Head Coach",
                phone_number="+96170111002",
                date_of_birth=datetime(season_start - 12, 3, 18).date(),
            )
            staff_user, _ = _ensure_user(
                team=team,
                email="staff.lions@example.com",
                first_name="Sam",
                last_name="Staff",
                role=AccountProfile.ROLE_STAFF,
                password=DEFAULT_PASSWORD,
                member_title="Performance Staff",
                phone_number="+96170111003",
                date_of_birth=datetime(season_start - 9, 8, 7).date(),
            )

            player_specs = [
                ("player1.lions@example.com", "Hassan", "Yuta", "Outside Hitter", 11, "+96170111101"),
                ("player2.lions@example.com", "Fadi", "Al Dowri", "Setter", 3, "+96170111102"),
                ("player3.lions@example.com", "Tala", "Mansadar", "Libero", 7, "+96170111103"),
                ("player4.lions@example.com", "Mohamed", "Ransis", "Middle Blocker", 15, "+96170111104"),
                ("player5.lions@example.com", "Hussein", "Rashid", "Opposite Hitter", 9, "+96170111105"),
                ("player6.lions@example.com", "Karim", "Nader", "Outside Hitter", 13, "+96170111106"),
                ("player7.lions@example.com", "Nour", "Salem", "Setter", 5, "+96170111107"),
                ("player8.lions@example.com", "Yara", "Haddad", "Middle Blocker", 18, "+96170111108"),
            ]

            players = []
            for index, (email, first_name, last_name, position, jersey, phone) in enumerate(player_specs, start=1):
                birth_year = season_start - 8 - (index % 4)
                player_user, player_membership = _ensure_user(
                    team=team,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    role=AccountProfile.ROLE_PLAYER,
                    password=DEFAULT_PASSWORD,
                    member_title="Captain" if index in {1, 4} else "Player",
                    position=position,
                    jersey_number=jersey,
                    phone_number=phone,
                    date_of_birth=datetime(birth_year, (index % 12) + 1, min(20, 8 + index)).date(),
                )
                players.append((player_user, player_membership))

            parent_user, parent_membership = _ensure_user(
                team=team,
                email="parent.lions@example.com",
                first_name="Nadia",
                last_name="Parent",
                role=AccountProfile.ROLE_PARENT,
                password=DEFAULT_PASSWORD,
                member_title="Parent",
                phone_number="+96170111999",
                date_of_birth=datetime(season_start - 20, 11, 6).date(),
            )
            parent_profile = parent_user.profile
            primary_child_profile = players[0][0].profile
            secondary_child_profile = players[1][0].profile
            parent_profile.child_name = primary_child_profile.user.get_full_name().strip()
            parent_profile.linked_player = primary_child_profile
            parent_profile.save(update_fields=["child_name", "linked_player"])
            ParentChildLink.objects.get_or_create(
                parent_profile=parent_profile,
                child_profile=primary_child_profile,
            )
            ParentChildLink.objects.get_or_create(
                parent_profile=parent_profile,
                child_profile=secondary_child_profile,
            )

            prefs_updates = [
                (manager_user, {"announcement_digest": NotificationPreferences.DIGEST_INSTANT}),
                (coach_user, {"announcement_digest": NotificationPreferences.DIGEST_INSTANT}),
                (
                    parent_user,
                    {
                        "quiet_hours_enabled": True,
                        "quiet_skip_entirely": False,
                    },
                ),
            ]
            for user, values in prefs_updates:
                prefs = NotificationPreferences.for_user(user)
                for field, value in values.items():
                    setattr(prefs, field, value)
                prefs.save()

            total_headcount = TeamMembership.objects.filter(
                team=team,
                status=TeamMembership.STATUS_APPROVED,
                is_active=True,
            ).count()

            historical_specs = [
                ("Season Opener vs Cedars", ScheduledEvent.TYPE_MATCH, _aware(season_start, 9, 14, 19, 0), "Beirut Sports Arena", ScheduledEvent.AUDIENCE_ALL, 110),
                ("Recovery Practice - Week 1", ScheduledEvent.TYPE_PRACTICE, _aware(season_start, 9, 17, 18, 30), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 90),
                ("Serve & Receive Lab", ScheduledEvent.TYPE_TRAINING, _aware(season_start, 10, 2, 18, 0), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 80),
                ("Captain Strategy Review", ScheduledEvent.TYPE_OTHER, _aware(season_start, 10, 9, 19, 15), "Team Tactics Room", ScheduledEvent.AUDIENCE_MANAGERS, 50),
                ("League Match vs Falcons", ScheduledEvent.TYPE_MATCH, _aware(season_start, 10, 19, 20, 0), "Tripoli Arena", ScheduledEvent.AUDIENCE_ALL, 120),
                ("Rotation Review Practice", ScheduledEvent.TYPE_PRACTICE, _aware(season_start, 11, 5, 18, 30), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 95),
                ("Serve Pressure Scrimmage", ScheduledEvent.TYPE_TRAINING, _aware(season_start, 11, 14, 18, 15), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 85),
                ("Tournament Pool Match", ScheduledEvent.TYPE_TOURNAMENT, _aware(season_start, 11, 28, 17, 30), "North Sports Complex", ScheduledEvent.AUDIENCE_ALL, 130),
                ("Defense Intensive", ScheduledEvent.TYPE_TRAINING, _aware(season_start, 12, 10, 18, 0), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 85),
                ("Assistant Coaches Briefing", ScheduledEvent.TYPE_OTHER, _aware(season_start, 12, 15, 20, 0), "Club Lounge", ScheduledEvent.AUDIENCE_COACHES, 45),
                ("Winter Classic Match", ScheduledEvent.TYPE_MATCH, _aware(season_start, 12, 22, 19, 30), "Sidon Indoor Hall", ScheduledEvent.AUDIENCE_ALL, 115),
                ("January Return Practice", ScheduledEvent.TYPE_PRACTICE, _aware(season_start + 1, 1, 8, 18, 30), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 90),
                ("Video Breakdown Session", ScheduledEvent.TYPE_OTHER, _aware(season_start + 1, 1, 15, 19, 0), "Analysis Room", ScheduledEvent.AUDIENCE_ALL, 60),
                ("Midseason Tournament", ScheduledEvent.TYPE_TOURNAMENT, _aware(season_start + 1, 1, 24, 16, 0), "Central Championship Hall", ScheduledEvent.AUDIENCE_ALL, 140),
                ("Block Timing Clinic", ScheduledEvent.TYPE_TRAINING, _aware(season_start + 1, 2, 6, 18, 0), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 85),
                ("Parent Touchpoint Meeting", ScheduledEvent.TYPE_OTHER, _aware(season_start + 1, 2, 12, 18, 45), "Club Meeting Space", ScheduledEvent.AUDIENCE_PARENTS, 45),
                ("Top-of-Table Match", ScheduledEvent.TYPE_MATCH, _aware(season_start + 1, 2, 21, 20, 0), "Beirut Sports Arena", ScheduledEvent.AUDIENCE_ALL, 120),
                ("March Systems Practice", ScheduledEvent.TYPE_PRACTICE, _aware(season_start + 1, 3, 6, 18, 30), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 90),
                ("Bench Unit Scrimmage", ScheduledEvent.TYPE_TRAINING, _aware(season_start + 1, 3, 13, 18, 15), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 90),
                ("Regional Semi Final", ScheduledEvent.TYPE_MATCH, _aware(season_start + 1, 3, 21, 19, 30), "Regional Arena", ScheduledEvent.AUDIENCE_ALL, 125),
                ("April Form Check", ScheduledEvent.TYPE_TRAINING, _aware(season_start + 1, 4, 9, 18, 0), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 80),
                ("Spring Showcase Match", ScheduledEvent.TYPE_MATCH, _aware(season_start + 1, 4, 18, 20, 0), "Lions Main Court", ScheduledEvent.AUDIENCE_ALL, 115),
                ("Recovery Mobility Session", ScheduledEvent.TYPE_PRACTICE, now_local - timedelta(days=8, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 70),
                ("Late-Season Tactical Practice", ScheduledEvent.TYPE_TRAINING, now_local - timedelta(days=5, hours=1), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 95),
                ("Closed-Door Scrimmage", ScheduledEvent.TYPE_MATCH, now_local - timedelta(days=3, hours=3), "National Court", ScheduledEvent.AUDIENCE_ALL, 110),
                ("Scouting Walkthrough", ScheduledEvent.TYPE_OTHER, now_local - timedelta(days=1, hours=4), "Analysis Room", ScheduledEvent.AUDIENCE_COACHES, 55),
            ]

            future_specs = [
                ("Video Review Session", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=1, hours=3), "Team Meeting Room", ScheduledEvent.AUDIENCE_COACHES, 60),
                ("Light Recovery Practice", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=2, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 75),
                ("Serve Pressure Training", ScheduledEvent.TYPE_TRAINING, now_local + timedelta(days=5, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 90),
                ("Parent Briefing", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=6, hours=1), "Club Lounge", ScheduledEvent.AUDIENCE_PARENTS, 45),
                ("Scouting Review", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=7, hours=3), "Analytics Room", ScheduledEvent.AUDIENCE_STAFF, 50),
                ("Main Match vs Waves", ScheduledEvent.TYPE_MATCH, now_local + timedelta(days=9, hours=4), "National Court", ScheduledEvent.AUDIENCE_ALL, 120),
                ("Post-Match Recovery", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=11, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 70),
                ("Mini Tournament", ScheduledEvent.TYPE_TOURNAMENT, now_local + timedelta(days=16, hours=4), "Metro Sports Dome", ScheduledEvent.AUDIENCE_ALL, 150),
                ("Captain Leadership Check-In", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=18, hours=2), "Strategy Room", ScheduledEvent.AUDIENCE_MANAGERS, 45),
                ("Travel Preparation Practice", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=22, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 95),
                ("Serve Pressure Rematch", ScheduledEvent.TYPE_TRAINING, now_local + timedelta(days=24, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 85),
                ("Sports Medicine Screening", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=26, hours=1), "Club Medical Office", ScheduledEvent.AUDIENCE_STAFF, 50),
                ("Away Match vs Rangers", ScheduledEvent.TYPE_MATCH, now_local + timedelta(days=29, hours=4), "Rangers Arena", ScheduledEvent.AUDIENCE_ALL, 125),
                ("Travel Recovery Session", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=31, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 70),
                ("May Tournament Qualifier", ScheduledEvent.TYPE_TOURNAMENT, now_local + timedelta(days=35, hours=5), "Capital Sports City", ScheduledEvent.AUDIENCE_ALL, 150),
                ("Parent Ops Briefing", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=37, hours=1), "Club Lounge", ScheduledEvent.AUDIENCE_PARENTS, 45),
                ("Assistant Coach Sync", ScheduledEvent.TYPE_OTHER, now_local + timedelta(days=40, hours=2), "Team Tactics Room", ScheduledEvent.AUDIENCE_COACHES, 50),
                ("Championship Prep Practice", ScheduledEvent.TYPE_PRACTICE, now_local + timedelta(days=43, hours=2), "Lions Main Court", ScheduledEvent.AUDIENCE_PLAYERS, 100),
            ]

            events = []
            for title, event_type, scheduled_at, location, audience, duration in historical_specs + future_specs:
                event = _ensure_event(
                    team=team,
                    created_by=coach_user if event_type != ScheduledEvent.TYPE_OTHER else manager_user,
                    title=title,
                    event_type=event_type,
                    scheduled_at=scheduled_at,
                    location=location,
                    audience=audience,
                    duration_minutes=duration,
                    details=f"Demo event for {title}.",
                    attendees_count=total_headcount,
                )
                events.append(event)

            historical_events = [event for event in events if event.scheduled_at <= timezone.now()]
            future_events = [event for event in events if event.scheduled_at > timezone.now()]

            player_count = len(players)
            for event_index, event in enumerate(events):
                for player_index, (player_user, player_membership) in enumerate(players):
                    selector = (event_index * 3 + player_index) % 11
                    if selector in {0, 7}:
                        status = EventAttendance.STATUS_NOT_ATTENDING
                        reason = EventAttendance.REASON_INJURED if selector == 7 else EventAttendance.REASON_OTHER
                        note = "Recovery protocol" if reason == EventAttendance.REASON_INJURED else "Family commitment"
                    elif selector in {3, 9}:
                        status = EventAttendance.STATUS_MAYBE
                        reason = ""
                        note = ""
                    else:
                        status = EventAttendance.STATUS_ATTENDING
                        reason = ""
                        note = ""

                    EventAttendance.objects.update_or_create(
                        event=event,
                        player=player_user,
                        defaults={
                            "status": status,
                            "not_attending_reason": reason,
                            "not_attending_reason_note": note,
                        },
                    )

                    if event in historical_events:
                        participation = TeamPerformanceRecord.PARTICIPATION_PRESENT
                        injury_status = ""
                        if status == EventAttendance.STATUS_NOT_ATTENDING:
                            if reason == EventAttendance.REASON_INJURED:
                                participation = TeamPerformanceRecord.PARTICIPATION_INJURED
                                injury_status = random.choice(
                                    [
                                        TeamPerformanceRecord.INJURY_MINOR_ISSUE,
                                        TeamPerformanceRecord.INJURY_RECOVERING,
                                        TeamPerformanceRecord.INJURY_RECENTLY_INJURED,
                                    ]
                                )
                            else:
                                participation = TeamPerformanceRecord.PARTICIPATION_DID_NOT_ATTEND
                        elif status == EventAttendance.STATUS_MAYBE:
                            participation = TeamPerformanceRecord.PARTICIPATION_ABSENT

                        base_kills = 0
                        base_aces = 0
                        base_blocks = 0
                        base_assists = 0
                        base_digs = 0
                        base_errors = 0
                        points_scored = 0
                        points_conceded = 0

                        if participation == TeamPerformanceRecord.PARTICIPATION_PRESENT:
                            position = player_user.profile.position.lower()
                            attacker_bonus = 4 if "hitter" in position else 2
                            blocker_bonus = 4 if "middle blocker" in position else 1
                            setter_bonus = 5 if "setter" in position else 1
                            libero_bonus = 6 if "libero" in position else 1
                            base_kills = 6 + attacker_bonus + (event_index + player_index) % 6
                            base_aces = 1 + (player_index + event_index) % 4
                            base_blocks = blocker_bonus + (event_index % 3)
                            base_assists = setter_bonus + (event_index + player_index) % 5
                            base_digs = libero_bonus + (player_index % 5)
                            base_errors = 1 + ((event_index * 2 + player_index) % 4)
                            points_scored = 18 + (event_index % 9) + (player_index % 5)
                            points_conceded = 13 + ((event_index + player_index) % 8)

                        if event.event_type in {ScheduledEvent.TYPE_MATCH, ScheduledEvent.TYPE_TOURNAMENT}:
                            result_cycle = [
                                TeamPerformanceRecord.RESULT_WIN,
                                TeamPerformanceRecord.RESULT_WIN,
                                TeamPerformanceRecord.RESULT_LOSS,
                                TeamPerformanceRecord.RESULT_DRAW,
                            ]
                            result = result_cycle[event_index % len(result_cycle)]
                        else:
                            result = TeamPerformanceRecord.RESULT_WIN

                        TeamPerformanceRecord.objects.update_or_create(
                            event=event,
                            member=player_membership,
                            defaults={
                                "team": team,
                                "recorded_by": coach_user,
                                "result": result,
                                "participation_status": participation,
                                "injury_status": injury_status,
                                "points_scored": points_scored,
                                "points_conceded": points_conceded,
                                "target_score": 20,
                                "target_achieved": participation == TeamPerformanceRecord.PARTICIPATION_PRESENT and points_scored >= 20,
                                "kills": base_kills,
                                "aces": base_aces,
                                "blocks": base_blocks,
                                "assists": base_assists,
                                "digs": base_digs,
                                "unforced_errors": base_errors,
                                "notes": f"Full demo seed for {event.title}.",
                            },
                        )

            for event in future_events[:3]:
                EventNotificationRead.objects.update_or_create(
                    user=manager_user,
                    event=event,
                    defaults={"is_deleted": False},
                )
            if len(future_events) >= 4:
                EventNotificationRead.objects.update_or_create(
                    user=coach_user,
                    event=future_events[3],
                    defaults={"is_deleted": True},
                )

            announcement_specs = [
                ("Weekly Training Plan", "Updated weekly schedule is ready. Check recovery blocks and match prep windows.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_INFO, True, now_local - timedelta(days=8)),
                ("Starting Lineup Review", "Coaches will review rotation priorities after Thursday practice.", Announcement.AUDIENCE_PLAYERS, Announcement.PRIORITY_IMPORTANT, False, now_local - timedelta(days=5)),
                ("Urgent Venue Change", "The next match has been moved to National Court. Arrival is 45 minutes earlier.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_URGENT, True, now_local - timedelta(days=3)),
                ("Parent Transport Form", "Please confirm transport and pickup details before Friday night.", Announcement.AUDIENCE_PARENTS, Announcement.PRIORITY_IMPORTANT, False, now_local - timedelta(days=2)),
                ("Staff Medical Check", "Update recovery notes and player restrictions before the next training block.", Announcement.AUDIENCE_STAFF, Announcement.PRIORITY_INFO, False, now_local - timedelta(days=1, hours=5)),
                ("Manager Operations Note", "Court booking and jersey inventory have been finalized for the upcoming tournament.", Announcement.AUDIENCE_MANAGERS, Announcement.PRIORITY_INFO, False, now_local - timedelta(hours=10)),
                ("Captain Focus Board", "Captains should keep the energy high and lead transitions during the next three sessions.", Announcement.AUDIENCE_PLAYERS, Announcement.PRIORITY_INFO, False, now_local - timedelta(hours=18)),
                ("Recovery Window Update", "The recovery block after the last scrimmage is extended for athletes flagged by staff.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_IMPORTANT, False, now_local - timedelta(hours=14)),
                ("Tournament Checklist", "Bring travel documents, two jerseys, water bottle, and recovery band kit for the qualifier trip.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_INFO, False, now_local - timedelta(hours=9)),
                ("Parent Arrival Reminder", "Pickup timing has shifted by fifteen minutes after the next away match.", Announcement.AUDIENCE_PARENTS, Announcement.PRIORITY_INFO, False, now_local - timedelta(hours=6)),
                ("Coach Rotation Notes", "Review the latest rotation notes before tonight's tactical block.", Announcement.AUDIENCE_COACHES, Announcement.PRIORITY_IMPORTANT, False, now_local - timedelta(hours=4)),
                ("Team Focus For This Week", "First-ball contact and quick transition defense are the main focus areas this week.", Announcement.AUDIENCE_ALL, Announcement.PRIORITY_INFO, True, now_local - timedelta(hours=2)),
            ]

            active_memberships = list(
                TeamMembership.objects.filter(
                    team=team,
                    status=TeamMembership.STATUS_APPROVED,
                    is_active=True,
                ).select_related("user__profile")
            )

            announcements = []
            for index, (title, body, audience, priority, pin_to_top, created_at) in enumerate(announcement_specs, start=1):
                announcement, _ = Announcement.objects.get_or_create(
                    team=team,
                    created_by=coach_user if audience != Announcement.AUDIENCE_MANAGERS else manager_user,
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
                announcement.body = body
                announcement.audience = audience
                announcement.priority = priority
                announcement.send_push_notification = True
                announcement.send_email_notification = True
                announcement.send_sms_notification = False
                announcement.pin_to_top = pin_to_top
                announcement.save()
                Announcement.objects.filter(pk=announcement.pk).update(created_at=created_at)
                announcement.refresh_from_db()
                announcements.append(announcement)

                target_role = _announcement_target_role(audience)
                for membership in active_memberships:
                    if target_role and membership.user.profile.role != target_role and membership.user_id != announcement.created_by_id:
                        continue
                    recipient, _ = AnnouncementRecipient.objects.get_or_create(
                        announcement=announcement,
                        user=membership.user,
                    )
                    recipient.is_deleted = False
                    if membership.user_id == announcement.created_by_id:
                        recipient.read_at = created_at + timedelta(minutes=2)
                    elif (membership.id + index) % 3 == 0:
                        recipient.read_at = created_at + timedelta(hours=4)
                    else:
                        recipient.read_at = None
                    recipient.save()

                top_comment, _ = AnnouncementComment.objects.get_or_create(
                    announcement=announcement,
                    author=manager_user,
                    parent=None,
                    body=f"Manager acknowledgement for {title}.",
                )
                AnnouncementComment.objects.get_or_create(
                    announcement=announcement,
                    author=staff_user,
                    parent=top_comment,
                    body=f"Staff follow-up on {title}.",
                )
                second_comment, _ = AnnouncementComment.objects.get_or_create(
                    announcement=announcement,
                    author=players[index % player_count][0],
                    parent=None,
                    body=f"Player confirmation for {title}.",
                )
                AnnouncementComment.objects.get_or_create(
                    announcement=announcement,
                    author=coach_user,
                    parent=second_comment,
                    body=f"Coach reply on {title}.",
                )
                AnnouncementComment.objects.get_or_create(
                    announcement=announcement,
                    author=parent_user if audience in {Announcement.AUDIENCE_ALL, Announcement.AUDIENCE_PARENTS} else players[(index + 1) % player_count][0],
                    parent=None,
                    body=f"Additional follow-up on {title}.",
                )

                message, _ = AnnouncementMessage.objects.get_or_create(
                    announcement=announcement,
                    author=announcement.created_by,
                    body=f"Follow-up message thread for {title}.",
                )
                AnnouncementMessageRead.objects.get_or_create(message=message, user=announcement.created_by)
                AnnouncementMessageRead.objects.get_or_create(
                    message=message,
                    user=players[index % player_count][0],
                )
                second_message, _ = AnnouncementMessage.objects.get_or_create(
                    announcement=announcement,
                    author=players[(index + 2) % player_count][0],
                    body=f"Player thread response for {title}.",
                )
                AnnouncementMessageRead.objects.get_or_create(message=second_message, user=announcement.created_by)
                AnnouncementMessageRead.objects.get_or_create(
                    message=second_message,
                    user=players[(index + 3) % player_count][0],
                )

            private_message_specs = [
                (coach_user, players[0][0], "Strong hitting session today. Keep your approach compact.", now_local - timedelta(days=6)),
                (players[0][0], coach_user, "Thanks coach, I will focus on the last two steps.", now_local - timedelta(days=6, minutes=-18)),
                (coach_user, players[0][0], "Good. We will review it again before match day.", now_local - timedelta(days=5, hours=20)),
                (manager_user, parent_user, "Please confirm transport details for Saturday.", now_local - timedelta(days=4)),
                (parent_user, manager_user, "Confirmed. Pickup details are submitted.", now_local - timedelta(days=4, minutes=-35)),
                (staff_user, players[2][0], "Recovery check: how is the shoulder today?", now_local - timedelta(days=2, hours=5)),
                (players[2][0], staff_user, "Much better. I can train fully tomorrow.", now_local - timedelta(days=2, hours=4, minutes=20)),
                (players[4][0], coach_user, "I might be late to the first drill block.", now_local - timedelta(hours=22)),
                (coach_user, players[4][0], "Understood. Warm up with staff when you arrive.", now_local - timedelta(hours=21, minutes=15)),
                (players[6][0], players[7][0], "Want to review serve targets before practice?", now_local - timedelta(hours=9)),
                (manager_user, coach_user, "Please post the travel timings after the final staff check.", now_local - timedelta(hours=8, minutes=40)),
                (coach_user, manager_user, "Will do. I am updating the announcement board now.", now_local - timedelta(hours=8, minutes=10)),
                (staff_user, coach_user, "Three players are marked for lighter recovery tonight.", now_local - timedelta(hours=7, minutes=20)),
                (coach_user, staff_user, "Perfect. I will split their reps during the tactical block.", now_local - timedelta(hours=7)),
                (players[1][0], players[3][0], "Let's review block timing clips before the next scrimmage.", now_local - timedelta(hours=5, minutes=15)),
                (players[3][0], players[1][0], "Yes, I saved the video markers already.", now_local - timedelta(hours=5)),
                (parent_user, players[0][0], "Good luck tonight. Remember your recovery band.", now_local - timedelta(hours=4, minutes=30)),
                (players[0][0], parent_user, "Thanks! I packed everything.", now_local - timedelta(hours=4, minutes=2)),
                (players[5][0], coach_user, "Can we review my serve toss after training?", now_local - timedelta(hours=2, minutes=45)),
                (coach_user, players[5][0], "Yes, stay five minutes after the session and we will fix it.", now_local - timedelta(hours=2, minutes=20)),
            ]

            for sender, recipient, body, created_at in private_message_specs:
                message, _ = PrivateMessage.objects.get_or_create(
                    sender=sender,
                    recipient=recipient,
                    team=team,
                    body=body,
                )
                read_at = None
                if recipient in {coach_user, manager_user, staff_user} or body.startswith("Confirmed"):
                    read_at = created_at + timedelta(minutes=25)
                PrivateMessage.objects.filter(pk=message.pk).update(
                    created_at=created_at,
                    read_at=read_at,
                )

            presence_fresh_users = [
                manager_user,
                coach_user,
                staff_user,
                players[0][0],
                players[1][0],
                players[2][0],
            ]
            for offset, user in enumerate(presence_fresh_users):
                UserPresence.objects.update_or_create(
                    user=user,
                    defaults={"last_seen": timezone.now() - timedelta(seconds=offset * 25)},
                )
            for offset, (user, _) in enumerate(players[3:], start=1):
                UserPresence.objects.update_or_create(
                    user=user,
                    defaults={"last_seen": timezone.now() - timedelta(minutes=12 + offset)},
                )
            UserPresence.objects.update_or_create(
                user=parent_user,
                defaults={"last_seen": timezone.now() - timedelta(minutes=6)},
            )

            _seed_secondary_club(
                team_name="Falcons",
                slug="falcons",
                created_by=admin_user,
                season_start=season_start,
                now_local=now_local,
                manager_spec={
                    "email": "manager.falcons@example.com",
                    "first_name": "Rami",
                    "last_name": "Falcons",
                    "phone": "+96170222001",
                    "dob": datetime(season_start - 11, 4, 8).date(),
                },
                coach_spec={
                    "email": "coach.falcons@example.com",
                    "first_name": "Dana",
                    "last_name": "Falcons",
                    "phone": "+96170222002",
                    "dob": datetime(season_start - 13, 1, 19).date(),
                },
                staff_spec={
                    "email": "staff.falcons@example.com",
                    "first_name": "Jad",
                    "last_name": "Falcons",
                    "phone": "+96170222003",
                    "dob": datetime(season_start - 10, 9, 13).date(),
                },
                parent_spec={
                    "email": "parent.falcons@example.com",
                    "first_name": "Maya",
                    "last_name": "Falcons",
                    "phone": "+96170222999",
                    "dob": datetime(season_start - 21, 6, 11).date(),
                },
                player_specs=[
                    {"email": "player1.falcons@example.com", "first_name": "Omar", "last_name": "Khaled", "position": "Outside Hitter", "jersey": 2, "phone": "+96170222101", "dob": datetime(season_start - 8, 2, 10).date(), "captain": True},
                    {"email": "player2.falcons@example.com", "first_name": "Lea", "last_name": "Marwan", "position": "Setter", "jersey": 4, "phone": "+96170222102", "dob": datetime(season_start - 7, 5, 16).date()},
                    {"email": "player3.falcons@example.com", "first_name": "Ziad", "last_name": "Nassif", "position": "Middle Blocker", "jersey": 8, "phone": "+96170222103", "dob": datetime(season_start - 8, 7, 18).date()},
                    {"email": "player4.falcons@example.com", "first_name": "Nadine", "last_name": "Saad", "position": "Libero", "jersey": 10, "phone": "+96170222104", "dob": datetime(season_start - 9, 3, 21).date()},
                    {"email": "player5.falcons@example.com", "first_name": "Tarek", "last_name": "Issa", "position": "Opposite Hitter", "jersey": 14, "phone": "+96170222105", "dob": datetime(season_start - 8, 10, 7).date()},
                    {"email": "player6.falcons@example.com", "first_name": "Mira", "last_name": "Habib", "position": "Outside Hitter", "jersey": 16, "phone": "+96170222106", "dob": datetime(season_start - 7, 12, 4).date()},
                ],
            )

            _seed_secondary_club(
                team_name="Waves",
                slug="waves",
                created_by=admin_user,
                season_start=season_start,
                now_local=now_local,
                manager_spec={
                    "email": "manager.waves@example.com",
                    "first_name": "Sami",
                    "last_name": "Waves",
                    "phone": "+96170333001",
                    "dob": datetime(season_start - 12, 8, 4).date(),
                },
                coach_spec={
                    "email": "coach.waves@example.com",
                    "first_name": "Hiba",
                    "last_name": "Waves",
                    "phone": "+96170333002",
                    "dob": datetime(season_start - 14, 2, 15).date(),
                },
                staff_spec={
                    "email": "staff.waves@example.com",
                    "first_name": "Khalil",
                    "last_name": "Waves",
                    "phone": "+96170333003",
                    "dob": datetime(season_start - 9, 11, 24).date(),
                },
                parent_spec={
                    "email": "parent.waves@example.com",
                    "first_name": "Rana",
                    "last_name": "Waves",
                    "phone": "+96170333999",
                    "dob": datetime(season_start - 20, 1, 30).date(),
                },
                player_specs=[
                    {"email": "player1.waves@example.com", "first_name": "Youssef", "last_name": "Bitar", "position": "Middle Blocker", "jersey": 5, "phone": "+96170333101", "dob": datetime(season_start - 8, 1, 9).date(), "captain": True},
                    {"email": "player2.waves@example.com", "first_name": "Lynn", "last_name": "Fares", "position": "Setter", "jersey": 6, "phone": "+96170333102", "dob": datetime(season_start - 7, 4, 14).date()},
                    {"email": "player3.waves@example.com", "first_name": "Hadi", "last_name": "Mokdad", "position": "Outside Hitter", "jersey": 9, "phone": "+96170333103", "dob": datetime(season_start - 8, 6, 19).date()},
                    {"email": "player4.waves@example.com", "first_name": "Sara", "last_name": "Jaber", "position": "Libero", "jersey": 11, "phone": "+96170333104", "dob": datetime(season_start - 9, 9, 8).date()},
                    {"email": "player5.waves@example.com", "first_name": "Ali", "last_name": "Hamdan", "position": "Opposite Hitter", "jersey": 17, "phone": "+96170333105", "dob": datetime(season_start - 8, 11, 12).date()},
                    {"email": "player6.waves@example.com", "first_name": "Jana", "last_name": "Maalouf", "position": "Outside Hitter", "jersey": 19, "phone": "+96170333106", "dob": datetime(season_start - 7, 12, 22).date()},
                ],
            )

        summary = {
            "users": User.objects.count(),
            "teams": Team.objects.count(),
            "memberships": TeamMembership.objects.count(),
            "events": ScheduledEvent.objects.count(),
            "attendance": EventAttendance.objects.count(),
            "performance_records": TeamPerformanceRecord.objects.count(),
            "announcements": Announcement.objects.count(),
            "announcement_comments": AnnouncementComment.objects.count(),
            "private_messages": PrivateMessage.objects.count(),
        }

        self.stdout.write(self.style.SUCCESS("Full demo seed complete."))
        for label, value in summary.items():
            self.stdout.write(f"- {label}: {value}")

        self.stdout.write("")
        self.stdout.write("Test credentials:")
        for email, password, role in [
            ("admin@example.com", ADMIN_PASSWORD, "admin"),
            ("manager.lions@example.com", DEFAULT_PASSWORD, "manager"),
            ("coach.lions@example.com", DEFAULT_PASSWORD, "coach"),
            ("staff.lions@example.com", DEFAULT_PASSWORD, "staff"),
            ("parent.lions@example.com", DEFAULT_PASSWORD, "parent"),
            ("player1.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player2.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player3.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player4.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player5.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player6.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player7.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("player8.lions@example.com", DEFAULT_PASSWORD, "player"),
            ("manager.falcons@example.com", DEFAULT_PASSWORD, "manager"),
            ("coach.falcons@example.com", DEFAULT_PASSWORD, "coach"),
            ("staff.falcons@example.com", DEFAULT_PASSWORD, "staff"),
            ("parent.falcons@example.com", DEFAULT_PASSWORD, "parent"),
            ("player1.falcons@example.com", DEFAULT_PASSWORD, "player"),
            ("manager.waves@example.com", DEFAULT_PASSWORD, "manager"),
            ("coach.waves@example.com", DEFAULT_PASSWORD, "coach"),
            ("staff.waves@example.com", DEFAULT_PASSWORD, "staff"),
            ("parent.waves@example.com", DEFAULT_PASSWORD, "parent"),
            ("player1.waves@example.com", DEFAULT_PASSWORD, "player"),
        ]:
            self.stdout.write(f"- {email} | {password} | {role}")
