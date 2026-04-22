import pickle
import numpy as np

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
import joblib
import os
from django.conf import settings
from team_management.models import TeamMembership
from performance.models import TeamPerformanceRecord
from django.utils import timezone
from scheduling.models import ScheduledEvent
import pandas as pd


import numpy as np

def safe_float(val, default=0.0):
    try:
        if val is None:
            return default
        if isinstance(val, str) and val.strip() == "":
            return default
        return float(val)
    except:
        return default

MODEL_PATH = os.path.join(settings.BASE_DIR, "models", "best_match_readiness_model.joblib")
loaded = joblib.load(MODEL_PATH)
best_model = loaded["pipeline"]

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

    latest_record = (
    TeamPerformanceRecord.objects
    .filter(team=user_team)
    .select_related("event")
    .order_by("-event__scheduled_at")
    .first()
)

    latest_match_date = (
        latest_record.event.scheduled_at
        if latest_record else None
    )
    print(f"Latest match date for team {user_team.name}: {latest_match_date}")
    next_match = (
    ScheduledEvent.objects
    .filter(
        team=user_team,
        event_type=ScheduledEvent.TYPE_MATCH,
        status=ScheduledEvent.STATUS_SCHEDULED,
        scheduled_at__gt=latest_match_date,
    )
    .order_by("scheduled_at")
    .first()
    )
    next_match_date = (
        next_match.scheduled_at
        if next_match else None
    )
    if next_match_date and latest_match_date:
        days_between = (next_match_date - latest_match_date).days
    else:
        days_between = 7  # default to 7 if we can't calculate
    print(f"Next match for team {user_team.name}: {next_match}")
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

        # # ── READINESS SCORE (0–100) ───────────────────
        # readiness_score = round(min(
        #     attendance      * 100 * 0.40 +
        #     performance_pct       * 0.40 +
        #     health_score    * 100 * 0.20,
        #     100
        # ), 1)

        latest_record = (
            TeamPerformanceRecord.objects
            .filter(member=member, team=user_team)
            .select_related("event")
            .order_by("-event__scheduled_at")
            .first()
        )
        print(f"Latest record for {member.user.get_full_name() or member.user.username}: {latest_record}")
        prev = latest_record
        print(prev.points_scored if prev else "No previous record")
        ml_input = {
    "position": member.user.profile.position or "Player",

    "previous_participation_status": prev.participation_status if prev else "absent",
    "previous_injury_status": prev.injury_status if prev else "",
    "previous_match_result": prev.result if prev else "draw",

    "points_scored": prev.points_scored if prev else 0,
    "points_conceded": prev.points_conceded if prev else 0,

    "previous_point_difference": safe_float(
        (prev.points_scored - prev.points_conceded) if prev else 0
    ),

    "target_achieved": int(prev.target_achieved if prev else 0),

    "kills": prev.kills if prev else 0,
    "aces": prev.aces if prev else 0,
    "blocks": prev.blocks if prev else 0,
    "assists": prev.assists if prev else 0,
    "digs": prev.digs if prev else 0,
    "unforced_errors": prev.unforced_errors if prev else 0,
    "previous_total_positive_actions": safe_float(
        (prev.kills + prev.aces + prev.blocks + prev.assists + prev.digs)
        if prev else 0
    ),

    "previous_error_ratio": safe_float(
        (
            prev.unforced_errors /
            max(
                (prev.kills + prev.aces + prev.blocks + prev.assists + prev.digs)
                + prev.unforced_errors,
                1
            )
        ) if prev else 0
    ),

    "attendance_score": safe_float(attendance * 100),
    "performance_score": safe_float(performance_pct),
    "health_score": safe_float(health_score * 100),

    "days_until_next_match": safe_float(days_between
    ),
}
        print(f"ML Input for {member.user.get_full_name() or member.user.username}: {ml_input}")
        input_df = pd.DataFrame([ml_input])
        input_df = input_df.reindex(columns=loaded["feature_cols"])
        input_df = input_df.fillna(0)    
        probs = best_model.predict_proba(input_df)[0]
        classes = best_model.classes_

        probability_map = dict(zip(classes, probs))
        print(probability_map)

        readiness_score = (
            probability_map.get("Ready", 0) * 100 +
            probability_map.get("Needs Monitoring", 0) * 55 +
            probability_map.get("Not Ready", 0) * 15
        )
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

            "readiness":     round(readiness_score, 1),  # Convert to percentage
            "avatar": avatar_url,
        })

    return render(request, "matchreadiness/matchreadiness.html", {
        "players": players,
        "next_match": next_match,
    })