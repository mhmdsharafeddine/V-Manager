import pickle
import numpy as np

from django.shortcuts import render
from django.contrib.auth.decorators import login_required

from team_management.models import TeamMembership
from performance.models import TeamPerformanceRecord
from django.utils import timezone
from scheduling.models import ScheduledEvent


HEALTH_MAP = {
    "":                 ("healthy",    "Healthy"),
    "minor_issue":      ("minor",      "Minor"),
    "recovering":       ("recovering", "Recovering"),
    "recently_injured": ("recovering", "Recently Injured"),
}


@login_required
def match_readiness_page(request):
    



    user_membership = TeamMembership.objects.select_related("team").get(user=request.user)
    user_team = user_membership.team

    next_match = (
    ScheduledEvent.objects
    .filter(
        team=user_team,
        event_type=ScheduledEvent.TYPE_MATCH,
        status=ScheduledEvent.STATUS_SCHEDULED,
        scheduled_at__gte=timezone.now(),
    )
    .order_by("scheduled_at")
    .first()
    )
    memberships = TeamMembership.objects.select_related("user", "team").filter(
        team=user_team,
        requested_role="player"           # adjust to your actual TeamMembership.role field/value
    )

    # Total distinct events for this team only
    total_games = (
        TeamPerformanceRecord.objects
        .filter(team=user_team)
        .values("event")
        .distinct()
        .count()
    ) or 1

    players = []

    for member in memberships:
        try:
            photo = member.user.profile.profile_photo
            avatar_url = photo.url if photo else ""
        except Exception:
            avatar_url = ""
        records = TeamPerformanceRecord.objects.filter(member=member)

        # ── STATS ─────────────────────────────────────
        kills  = sum(r.kills  for r in records)
        aces   = sum(r.aces   for r in records)
        blocks = sum(r.blocks for r in records)

        # Only count games the player actually showed up to
        games_attended = records.filter(
            participation_status=TeamPerformanceRecord.PARTICIPATION_PRESENT
        ).values("event").distinct().count()

        attendance = games_attended / total_games

        # ── INJURY / HEALTH ───────────────────────────
        latest = records.order_by("-id").first()

        # injury_status is blank ("") when healthy
        raw_injury = (latest.injury_status if latest else "") or ""

        css_class, display_label = HEALTH_MAP.get(raw_injury, ("healthy", "Healthy"))

        health_score = {
            "":                 1.0,
            "minor_issue":      0.8,
            "recovering":       0.5,
            "recently_injured": 0.2,
        }.get(raw_injury, 1.0)

        # ── LAST MATCH DATE ───────────────────────────
        # ScheduledEvent uses scheduled_at, not date
        last_match_date = (
            latest.event.scheduled_at.strftime("%d %b %Y")
            if latest and hasattr(latest.event, "scheduled_at")
            else "N/A"
        )

        # ── PERFORMANCE % ────────────────────────────
        games_safe = games_attended or 1
        kill_pct  = min(kills  / (games_safe * 5), 1.0) * 100
        ace_pct   = min(aces   / (games_safe * 3), 1.0) * 100
        block_pct = min(blocks / (games_safe * 4), 1.0) * 100
        performance_pct = round(kill_pct * 0.5 + ace_pct * 0.3 + block_pct * 0.2, 1)

        # ── READINESS SCORE (0–100) ───────────────────
        readiness_score = round(min(
            attendance      * 100 * 0.40 +
            performance_pct       * 0.40 +
            health_score    * 100 * 0.20,
            100
        ), 1)

        players.append({
            "id":            member.id,
            "name":          member.user.get_full_name() or member.user.username,
            "position":      member.member_title or "Player",

            "kills":         kills,
            "aces":          aces,
            "blocks":        blocks,

            "attendance":    round(attendance * 100, 1),
            "performance":   performance_pct,
            "health_score":  round(health_score * 100),
            "health":        display_label,   # e.g. "Healthy", "Recovering"
            "health_class":  css_class,       # e.g. "healthy", "recovering", "minor"

            "matches_played": games_attended,
            "last_match":    last_match_date,

            "readiness":     readiness_score,
            "avatar": avatar_url,
        })

    return render(request, "matchreadiness/matchreadiness.html", {
        "players": players,
        "next_match": next_match,
    })