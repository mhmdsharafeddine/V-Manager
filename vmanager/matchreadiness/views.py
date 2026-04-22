import pickle
import numpy as np

from django.shortcuts import render
from django.http import JsonResponse

from team_management.models import TeamMembership
from performance.models import TeamPerformanceRecord
from django.contrib.auth.decorators import login_required

@login_required
def match_readiness_page(request):
    
    user_membership = TeamMembership.objects.select_related("team").get(user=request.user)
    user_team = user_membership.team
    memberships = TeamMembership.objects.select_related("user", "team").filter(
    team=user_team,
    requested_role="player")
    players = []

    total_games = TeamPerformanceRecord.objects.values("event").distinct().count()
    total_games = total_games or 1

    for member in memberships:

        # ── PERFORMANCE DATA ──────────────────────────
        records = TeamPerformanceRecord.objects.filter(member=member)

        kills  = sum(r.kills  for r in records)
        aces   = sum(r.aces   for r in records)
        blocks = sum(r.blocks for r in records)

        games_played = records.values("event").distinct().count()
        attendance   = games_played / total_games

        # Latest record for injury + last match date
        latest = records.order_by("-id").first()
        injury_status = getattr(latest, "injury_status", "healthy") if latest else "healthy"
        last_match_date = (
            latest.event.date.strftime("%-d %b %Y")   # e.g. "8 Feb 2026"
            if latest and hasattr(latest.event, "date")
            else "N/A"
        )

        health_score = 1.0 if injury_status == "healthy" else 0.5

        # ── PERFORMANCE % ─────────────────────────────
        # Normalise against best possible in games played (tweak ceiling as needed)
        games_played_safe = games_played or 1
        max_kills_per_game  = 5
        max_aces_per_game   = 3
        max_blocks_per_game = 4

        kill_pct  = min(kills  / (games_played_safe * max_kills_per_game),  1.0) * 100
        ace_pct   = min(aces   / (games_played_safe * max_aces_per_game),   1.0) * 100
        block_pct = min(blocks / (games_played_safe * max_blocks_per_game), 1.0) * 100
        performance_pct = round((kill_pct * 0.5 + ace_pct * 0.3 + block_pct * 0.2), 1)

        # ── READINESS SCORE (0–100) ───────────────────
        raw = (
            attendance    * 100 * 0.40 +
            performance_pct      * 0.40 +
            health_score * 100 * 0.20
        )
        readiness_score = round(min(raw, 100), 1)

        players.append({
            "id":            member.id,
            "name":          member.user.get_full_name() or member.user.username,
            "position":      member.member_title or "Player",

            "kills":         kills,
            "aces":          aces,
            "blocks":        blocks,

            "attendance":    round(attendance * 100, 1),
            "performance":   performance_pct,          # ← was missing
            "health_score":  round(health_score * 100),# ← was missing (100 or 50)
            "health":        injury_status,

            "matches_played": games_played,            # ← was missing
            "last_match":    last_match_date,          # ← was missing

            "readiness":     readiness_score,
        })

    return render(request, "matchreadiness/matchreadiness.html", {
        "players": players
    })