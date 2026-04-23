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
POSITION_WEIGHTS = {
   
    "outside hitter":  (0.50, 0.25, 0.25),
    "opposite hitter": (0.55, 0.25, 0.20),
    "middle blocker":  (0.35, 0.20, 0.45),
    "setter":          (0.20, 0.45, 0.35),
    "libero":          (0.20, 0.45, 0.35),
    "right side":      (0.50, 0.25, 0.25),
    "player":          (0.40, 0.30, 0.30),  # default fallback
}

def compute_rating(kills_pct, aces_pct, blocks_pct, position="player"):
    kw, aw, bw = POSITION_WEIGHTS.get(position.lower().strip(), (0.40, 0.30, 0.30))
    return round(kills_pct * kw + aces_pct * aw + blocks_pct * bw, 1)

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
        min(100.0, round((values[i] / games[i]) / max_val * 100, 1)) if games[i] > 0 else 0
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
        games  = max(p["count"], 1)
        monthly = p["monthly"]
        position = p["position"]
        # ── Monthly pct arrays (same formula for both chart + overall) ──
        monthly_kill_pct  = to_pct(monthly["kills"],  monthly["games"], MAX_KILLS)
        monthly_ace_pct   = to_pct(monthly["aces"],   monthly["games"], MAX_ACES)
        monthly_block_pct = to_pct(monthly["blocks"], monthly["games"], MAX_BLOCKS)
        monthly_rating    = [
            compute_rating(monthly_kill_pct[i], monthly_ace_pct[i], monthly_block_pct[i],position)
            for i in range(6)
        ]

        # ── Overall rating = average of months that had games ──────────
        # This makes it consistent with what the chart shows
        played_ratings = [monthly_rating[i] for i in range(6) if monthly["games"][i] > 0]
        rating = round(sum(played_ratings) / len(played_ratings)) if played_ratings else 0

        # ── Season-total pcts (used only for skill tags) ───────────────
        kills_pct  = (p["kills"]  / games) / MAX_KILLS  * 100
        aces_pct   = (p["aces"]   / games) / MAX_ACES   * 100
        blocks_pct = (p["blocks"] / games) / MAX_BLOCKS * 100

        skills     = compute_skill_tags(p["kills"], p["aces"], p["blocks"], games, rating)
        best_skill = max(skills, key=lambda x: x["level"]) if skills else None

        players.append({
            "id":          p["id"],
            "name":        p["name"],
            "avatar":      p["avatar"],
            "position":    p["position"],
            "rating":      rating,               # ← now matches monthly scale
            "growth":      round((p["aces"] / games) * 10),
            "primary_tag": best_skill["name"]  if best_skill else "No Tag",
            "tag_icon":    best_skill["icon"]  if best_skill else "⭐",
            "tag_color":   best_skill["color"] if best_skill else "purple",
            "skills":      skills,
            "monthly_stats": {
                "attack_eff":  monthly_kill_pct,
                "serving_ace": monthly_ace_pct,
                "blocking":    monthly_block_pct,
                "rating":      monthly_rating,   # ← same compute_rating() used here
            },
        })
    

      

    players = sorted(players, key=lambda x: x["rating"], reverse=True)
    if len(players) >= 3:
        players = [players[0], players[len(players) // 2], players[-1]]
    elif len(players) == 2:
        players = [players[0], players[-1]]

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
        position = p.get("position", "Unknown")
        # Build prompt
        kw, aw, bw = POSITION_WEIGHTS.get(position.lower().strip(), (0.40, 0.30, 0.30))
        prompt = f"""
        You are an expert volleyball performance analyst reviewing a player's season data.

        PLAYER PROFILE:
        - Name: {name}
        - Position: {position} (Take this into account when analyzing their stats and giving insights)

        SKILL RATINGS (0-100%, based on per-game averages relative to max benchmarks):
        {chr(10).join(f"  - {s['name']}: {s['level']}%" for s in skills)}

        MONTHLY STATS (Sep → Feb, values are % of max benchmark per month, 0 = no games played):
        - Attack Efficiency (kills/game vs max {MAX_KILLS} kills): {stats.get('attack_eff', [])}
        - Serving Ace %    (aces/game  vs max {MAX_ACES}  aces):  {stats.get('serving_ace', [])}
        - Blocking %       (blocks/game vs max {MAX_BLOCKS} blocks): {stats.get('blocking', [])}
        - Overall Rating   (weighted: 40% attack + 30% ace + 30% block): {stats.get('rating', [])}

        Months with value 0 mean the player had no recorded games that month — ignore those months.
        The season runs September to February (6 months).

        OVERALL RATING (position-weighted: {int(kw*100)}% attack + {int(aw*100)}% ace + {int(bw*100)}% block):
        This weighting reflects the {position} role's priorities.

        Based on this data, give ONE coaching insight (2-3 sentences max, under 50 words).
        Focus on: their strongest skill, a specific trend you notice in the monthly data, and one actionable improvement.
        Be specific and realistic — avoid generic advice.
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