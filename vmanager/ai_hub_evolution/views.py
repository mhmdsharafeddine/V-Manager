# ai_hub/views.py
import json
import logging
import requests

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from performance.models import TeamPerformanceRecord
from team_management.access import can_access_advanced_analytics
from team_management.models import TeamMembership

logger = logging.getLogger(__name__)
MAX_KILLS = 20
MAX_ACES = 5
MAX_BLOCKS = 10
MONTHS = [9, 10, 11, 12, 1, 2, 3, 4]  # Sep to Apr
MONTH_LABELS = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr"]
MONTH_COUNT = len(MONTHS)
MONTH_INDEX = {m: i for i, m in enumerate(MONTHS)}
POSITION_WEIGHTS = {
    "outside hitter": (0.50, 0.25, 0.25),
    "opposite hitter": (0.55, 0.25, 0.20),
    "middle blocker": (0.35, 0.20, 0.45),
    "setter": (0.20, 0.45, 0.35),
    "libero": (0.20, 0.45, 0.35),
    "right side": (0.50, 0.25, 0.25),
    "player": (0.40, 0.30, 0.30),  # default fallback
}


def _deny_advanced_analytics_page(request):
    messages.error(request, "Players and parents cannot access AI Evolution Hub or Match Readiness.")
    return redirect("home")


def compute_rating(kills_pct, aces_pct, blocks_pct, position="player"):
    kw, aw, bw = POSITION_WEIGHTS.get(position.lower().strip(), (0.40, 0.30, 0.30))
    return round(kills_pct * kw + aces_pct * aw + blocks_pct * bw, 1)


def safe_avg(values, games):
    return [
        round(values[i] / games[i], 2) if games[i] > 0 else 0
        for i in range(MONTH_COUNT)
    ]


def compute_skill_tags(kills, aces, blocks, games, rating):
    def pct(value, max_value):
        per_game = value / max(games, 1)
        return min(100, round((per_game / max_value) * 100))

    tags = [
        {
            "name": "Ace Specialist",
            "icon": "🎯",
            "color": "orange",
            "level": pct(aces, MAX_ACES),
        },
        {
            "name": "Power Hitter",
            "icon": "💥",
            "color": "red",
            "level": pct(kills, MAX_KILLS),
        },
        {
            "name": "Block Wall",
            "icon": "🧱",
            "color": "blue",
            "level": pct(blocks, MAX_BLOCKS),
        },
        {
            "name": "Consistency",
            "icon": "⚡",
            "color": "purple",
            "level": min(100, round(rating)),
        },
    ]

    return tags


def to_pct(values, games, max_val):
    return [
        min(100.0, round((values[i] / games[i]) / max_val * 100, 1)) if games[i] > 0 else 0
        for i in range(MONTH_COUNT)
    ]


def _build_player_payload(*, member_id, name, avatar, position, kills, aces, blocks, count, monthly):
    games = max(count, 1)
    monthly_kill_pct = to_pct(monthly["kills"], monthly["games"], MAX_KILLS)
    monthly_ace_pct = to_pct(monthly["aces"], monthly["games"], MAX_ACES)
    monthly_block_pct = to_pct(monthly["blocks"], monthly["games"], MAX_BLOCKS)
    monthly_rating = [
        compute_rating(monthly_kill_pct[i], monthly_ace_pct[i], monthly_block_pct[i], position)
        for i in range(MONTH_COUNT)
    ]

    played_ratings = [monthly_rating[i] for i in range(MONTH_COUNT) if monthly["games"][i] > 0]
    rating = round(sum(played_ratings) / len(played_ratings)) if played_ratings else 0

    skills = compute_skill_tags(kills, aces, blocks, games, rating)
    best_skill = max(skills, key=lambda x: x["level"]) if skills else None

    return {
        "id": member_id,
        "name": name,
        "avatar": avatar,
        "position": position,
        "rating": rating,
        "growth": round((aces / games) * 10) if games else 0,
        "primary_tag": best_skill["name"] if best_skill else "No Tag",
        "tag_icon": best_skill["icon"] if best_skill else "⭐",
        "tag_color": best_skill["color"] if best_skill else "purple",
        "skills": skills,
        "monthly_stats": {
            "attack_eff": monthly_kill_pct,
            "serving_ace": monthly_ace_pct,
            "blocking": monthly_block_pct,
            "rating": monthly_rating,
        },
    }


@login_required
def home(request):
    if not can_access_advanced_analytics(request.user):
        return _deny_advanced_analytics_page(request)

    user = request.user
    membership = TeamMembership.objects.select_related("team").filter(
        user=user,
        is_active=True,
        status=TeamMembership.STATUS_APPROVED,
    ).first()
    team = membership.team if membership else None

    if team is not None:
        records = TeamPerformanceRecord.objects.select_related(
            "member",
            "member__user",
        ).filter(
            team=team
        ).filter(
            Q(event__scheduled_at__month__gte=9) |
            Q(event__scheduled_at__month__lte=4)
        )
    else:
        records = TeamPerformanceRecord.objects.none()

    player_map = {}
    team_totals = {
        "kills": 0,
        "aces": 0,
        "blocks": 0,
        "count": 0,
        "monthly": {
            "kills": [0] * MONTH_COUNT,
            "aces": [0] * MONTH_COUNT,
            "blocks": [0] * MONTH_COUNT,
            "games": [0] * MONTH_COUNT,
        },
    }

    for r in records:
        member = r.member
        user = member.user

        if member.id not in player_map:
            print(member.user.profile.position)
            player_map[member.id] = {
                "id": member.id,
                "name": user.get_full_name() or user.email,
                "avatar": (
                    user.profile.profile_photo.url
                    if hasattr(user, "profile") and user.profile.profile_photo
                    else ""
                ),
                "position": member.user.profile.position or "Player",
                "kills": 0,
                "aces": 0,
                "blocks": 0,
                "count": 0,
                "monthly": {
                    "kills": [0] * MONTH_COUNT,
                    "aces": [0] * MONTH_COUNT,
                    "blocks": [0] * MONTH_COUNT,
                    "games": [0] * MONTH_COUNT,
                },
            }

        p = player_map[member.id]
        p["kills"] += r.kills
        p["aces"] += r.aces
        p["blocks"] += r.blocks
        p["count"] += 1

        team_totals["kills"] += r.kills
        team_totals["aces"] += r.aces
        team_totals["blocks"] += r.blocks
        team_totals["count"] += 1

        month = r.event.scheduled_at.month

        if month in MONTH_INDEX:
            idx = MONTH_INDEX[month]

            p["monthly"]["kills"][idx] += r.kills
            p["monthly"]["aces"][idx] += r.aces
            p["monthly"]["blocks"][idx] += r.blocks
            p["monthly"]["games"][idx] += 1
            team_totals["monthly"]["kills"][idx] += r.kills
            team_totals["monthly"]["aces"][idx] += r.aces
            team_totals["monthly"]["blocks"][idx] += r.blocks
            team_totals["monthly"]["games"][idx] += 1

    players = []
    for p in player_map.values():
        players.append(
            _build_player_payload(
                member_id=p["id"],
                name=p["name"],
                avatar=p["avatar"],
                position=p["position"],
                kills=p["kills"],
                aces=p["aces"],
                blocks=p["blocks"],
                count=p["count"],
                monthly=p["monthly"],
            )
        )

    players = sorted(players, key=lambda x: x["rating"], reverse=True)

    team_name = team.name if team else "All Players Overview"

    team_overview = _build_player_payload(
        member_id="all",
        name=team_name,
        avatar="",
        position="Team-wide Evolution Hub",
        kills=team_totals["kills"],
        aces=team_totals["aces"],
        blocks=team_totals["blocks"],
        count=team_totals["count"],
        monthly=team_totals["monthly"],
    )
    team_overview["growth"] = round(team_overview["growth"])

    return render(request, "ai_evo_hub/ai_display.html", {
        "players": players,
        "team_overview": team_overview,
        "team_name": team_name,
    })


def player_monthly_stats(request, member_id):
    if not can_access_advanced_analytics(request.user):
        return JsonResponse({"error": "Forbidden."}, status=403)

    qs = (
        TeamPerformanceRecord.objects
        .filter(member_id=member_id)
        .annotate(month=TruncMonth("event__scheduled_at"))
        .values("month")
        .annotate(
            avg_kills=Avg("kills"),
            avg_aces=Avg("aces"),
            avg_blocks=Avg("blocks"),
        )
    )

    raw = {entry["month"].month: entry for entry in qs}

    data = {
        "labels": MONTH_LABELS,
        "kills": [],
        "aces": [],
        "blocks": [],
    }

    for m in MONTHS:
        entry = raw.get(m)
        data["kills"].append(entry["avg_kills"] if entry else 0)
        data["aces"].append(entry["avg_aces"] if entry else 0)
        data["blocks"].append(entry["avg_blocks"] if entry else 0)

    return JsonResponse(data)


def _generate_openrouter_insight(prompt):
    api_key = (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("OpenRouter API key is missing.")

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "inclusionai/ling-2.6-1t:free",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 70,
        },
        timeout=25,
    )

    try:
        result = response.json()
    except ValueError:
        result = {}

    if not response.ok:
        error_message = (
            (result.get("error") or {}).get("message")
            or response.text[:200]
            or f"HTTP {response.status_code}"
        )
        raise RuntimeError(f"OpenRouter {response.status_code}: {error_message}")

    content = (
        ((result.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    ).strip()
    if not content:
        raise RuntimeError("OpenRouter returned an empty insight.")

    return content


@csrf_exempt
@login_required
def generate_insights(request):
    if not can_access_advanced_analytics(request.user):
        return JsonResponse({"error": "Forbidden."}, status=403)

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    data = json.loads(request.body)
    players = data.get("players", [])
    insights = {}

    for p in players:
        name = p.get("name", "Player")
        skills = p.get("skills", [])
        stats = p.get("monthly_stats", {})
        position = p.get("position", "Unknown")
        kw, aw, bw = POSITION_WEIGHTS.get(position.lower().strip(), (0.40, 0.30, 0.30))
        prompt = f"""
        You are an expert volleyball performance analyst reviewing a player's season data.

        PLAYER PROFILE:
        - Name: {name}
        - Position: {position} (Take this into account when analyzing their stats and giving insights)

        SKILL RATINGS (0-100%, based on per-game averages relative to max benchmarks):
        {chr(10).join(f"  - {s['name']}: {s['level']}%" for s in skills)}

        MONTHLY STATS (Sep to Apr, values are % of max benchmark per month, 0 = no games played):
        - Attack Efficiency (kills/game vs max {MAX_KILLS} kills): {stats.get('attack_eff', [])}
        - Serving Ace %    (aces/game  vs max {MAX_ACES}  aces):  {stats.get('serving_ace', [])}
        - Blocking %       (blocks/game vs max {MAX_BLOCKS} blocks): {stats.get('blocking', [])}
        - Overall Rating   (weighted: 40% attack + 30% ace + 30% block): {stats.get('rating', [])}

        Months with value 0 mean the player had no recorded games that month. Ignore those months.
        The season runs September to April (8 months).

        OVERALL RATING (position-weighted: {int(kw*100)}% attack + {int(aw*100)}% ace + {int(bw*100)}% block):
        This weighting reflects the {position} role's priorities.

        Based on this data, give ONE coaching insight (2-3 sentences max, under 90 words).

        Focus on:
        - The player's biggest weakness or underperforming area
        - A clear pattern or trend from the monthly data that explains it
        - One specific thing they should focus on improving next

        Be easy, direct, specific, and actionable and use simple and easy words. Do not give generic advice.
        """

        try:
            insight = _generate_openrouter_insight(prompt)
        except Exception as e:
            logger.exception("Error generating AI insight for %s", name)
            insight = f"AI unavailable: {e}" if settings.DEBUG else "AI insight unavailable."

        insights[str(p["id"])] = insight

    return JsonResponse(insights)
