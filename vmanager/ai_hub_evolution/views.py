# ai_hub/views.py
from datetime import date

from django.shortcuts import render
from django.db.models import Q
from django.db.models import Avg, Sum
from team_management.models import TeamMembership
from performance.models import TeamPerformanceRecord
from django.contrib.auth.decorators import login_required
from django.conf import settings

OPENROUTER_API_KEY = settings.OPENROUTER_API_KEY
MAX_KILLS = 20
MAX_ACES = 5
MAX_BLOCKS = 10
MONTHS = [9, 10, 11, 12, 1, 2]  # Sep → Feb
MONTH_INDEX = {m: i for i, m in enumerate(MONTHS)}

def safe_avg(values, games):
    return [
        round(values[i] / games[i], 2) if games[i] > 0 else 0
        for i in range(6)
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
        round((values[i] / games[i]) / max_val * 100, 1) if games[i] > 0 else 0
        for i in range(6)
    ]

# Determine season start year
@login_required
def home(request):
    
    user = request.user

    if not user.is_authenticated:
        records = TeamPerformanceRecord.objects.none()
    else:
        team = user.team_membership.team  # adjust if needed

        records = TeamPerformanceRecord.objects.select_related(
            "member",
            "member__user",
        ).filter(
            team=team  
        ).filter(
            Q(event__scheduled_at__month__gte=9) |
            Q(event__scheduled_at__month__lte=2)
        )

    player_map = {}
    
    for r in records:
        member = r.member
        user = member.user
        
        if member.id not in player_map:
            print(member.user.profile.position)
            player_map[member.id] = {
                "id": member.id,
                "name": user.get_full_name() or user.email,
                "avatar": (user.profile.profile_photo.url if hasattr(user, "profile") and user.profile.profile_photo else ""),
                "position":member.user.profile.position  or "Player",
                "kills": 0,
                "aces": 0,
                "blocks": 0,
                "count": 0,

                "monthly": {
                    "kills": [0]*6,
                    "aces": [0]*6,
                    "blocks": [0]*6,
                    "games": [0]*6,
                }
            }

        p = player_map[member.id]
        p["kills"] += r.kills
        p["aces"] += r.aces
        p["blocks"] += r.blocks
        p["count"] += 1


        month = r.event.scheduled_at.month

        if month in MONTH_INDEX:
            idx = MONTH_INDEX[month]

            p["monthly"]["kills"][idx] += r.kills
            p["monthly"]["aces"][idx] += r.aces
            p["monthly"]["blocks"][idx] += r.blocks
            p["monthly"]["games"][idx] += 1

    players = []
    
    

    for p in player_map.values():
        
        games = max(p["count"], 1)
        kills_score = (p["kills"] / games) / MAX_KILLS * 100
        aces_score  = (p["aces"] / games) / MAX_ACES * 100
        blocks_score = (p["blocks"] / games) / MAX_BLOCKS * 100
        monthly = p["monthly"]
        rating = (
            kills_score * 0.4 +
            aces_score * 0.3 +
            blocks_score * 0.3
        )
        skills = compute_skill_tags(
        p["kills"], p["aces"], p["blocks"], games, rating
        )

       
        best_skill = max(skills, key=lambda x: x["level"]) if skills else None
    # ── ADD HERE ──────────────────────────────────────
        monthly_kill_pct  = to_pct(monthly["kills"],  monthly["games"], MAX_KILLS)
        monthly_ace_pct   = to_pct(monthly["aces"],   monthly["games"], MAX_ACES)
        monthly_block_pct = to_pct(monthly["blocks"], monthly["games"], MAX_BLOCKS)
        monthly_rating    = [
            round(monthly_kill_pct[i]*0.4 + monthly_ace_pct[i]*0.3 + monthly_block_pct[i]*0.3, 1)
            for i in range(6)
        ]

        players.append({
            "id": p["id"],
            "name": p["name"],
            "avatar": p["avatar"],
            "position": p["position"],
            "rating": round(rating),
            "growth": round((p["aces"] / games) * 10),
            "primary_tag": best_skill["name"] if best_skill else "No Tag",
            "tag_icon": best_skill["icon"] if best_skill else "⭐",
            "tag_color": best_skill["color"] if best_skill else "purple",

            "skills": compute_skill_tags(
            p["kills"], p["aces"], p["blocks"], games, rating
            ),

             "monthly_stats": {
            "attack_eff":  monthly_kill_pct,
            "serving_ace": monthly_ace_pct,
            "blocking":    monthly_block_pct,
            "rating":      monthly_rating,
        },
        })
    

      

    players = sorted(players, key=lambda x: x["rating"], reverse=True)[:3]

    return render(request, "ai_evo_hub/ai_display.html", {
        "players": players
    })

from django.http import JsonResponse
from django.db.models import Avg
from django.db.models.functions import TruncMonth

from performance.models import TeamPerformanceRecord



def player_monthly_stats(request, member_id):
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

    months_order = [9, 10, 11, 12, 1, 2]
    labels = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]

    data = {
        "labels": labels,
        "kills": [],
        "aces": [],
        "blocks": [],
    }

    for m in months_order:
        entry = raw.get(m)

        data["kills"].append(entry["avg_kills"] if entry else 0)
        data["aces"].append(entry["avg_aces"] if entry else 0)
        data["blocks"].append(entry["avg_blocks"] if entry else 0)

    return JsonResponse(data)


import json
import requests
from django.views.decorators.csrf import csrf_exempt




@csrf_exempt
def generate_insights(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    data = json.loads(request.body)
    players = data.get("players", [])
    insights = {}

    for p in players:
        name = p.get("name", "Player")
        skills = p.get("skills", [])
        stats = p.get("monthly_stats", {})

        # Build prompt
        prompt = f"""
        You are a professional volleyball coach.

        Player: {name}
        Position: {p.get("position", "Unknown")}
        Skills: {skills}
        Monthly Stats: {stats}

        Give ONE short performance insight (max 20 words).
        Be specific, analytical, and realistic.
        """

        print(f"Generated prompt for {name}: {prompt}")

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openai/gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 70
                }
            )

            result = response.json()
            print(f"AI response for {name}: {result}")
            insight = result["choices"][0]["message"]["content"].strip()

        except Exception as e:
            print(f"Error generating insight for {name}: {e}")
            insight = "AI insight unavailable."

        insights[str(p["id"])] = insight

    return JsonResponse(insights)