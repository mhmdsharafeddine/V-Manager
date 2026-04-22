from django.db.models import Avg, Sum
from performance.models import TeamPerformanceRecord


def get_player_aggregated_stats(member):
    qs = TeamPerformanceRecord.objects.filter(member=member)

    return qs.aggregate(
        avg_aces=Avg("aces"),
        avg_kills=Avg("kills"),
        avg_blocks=Avg("blocks"),
        avg_assists=Avg("assists"),
        avg_digs=Avg("digs"),
        avg_errors=Avg("unforced_errors"),
        total_matches=Sum("id"),  # just to know volume (optional)
    )


def assign_skill_tags(member):
    stats = get_player_aggregated_stats(member)

    tags = []

    # --- ACE SPECIALIST ---
    if stats["avg_aces"] and stats["avg_aces"] >= 3:
        tags.append({
            "name": "Ace Specialist",
            "score": min(100, stats["avg_aces"] * 20)
        })

    # --- POWER HITTER ---
    if stats["avg_kills"] and stats["avg_kills"] >= 10:
        tags.append({
            "name": "Power Hitter",
            "score": min(100, stats["avg_kills"] * 8)
        })

    # --- WALL BLOCKER ---
    if stats["avg_blocks"] and stats["avg_blocks"] >= 5:
        tags.append({
            "name": "Wall Blocker",
            "score": min(100, stats["avg_blocks"] * 15)
        })

    # --- PLAYMAKER ---
    if stats["avg_assists"] and stats["avg_assists"] >= 8:
        tags.append({
            "name": "Playmaker Pro",
            "score": min(100, stats["avg_assists"] * 10)
        })

    # --- DEFENSIVE ANCHOR ---
    if stats["avg_digs"] and stats["avg_digs"] >= 10:
        tags.append({
            "name": "Defensive Anchor",
            "score": min(100, stats["avg_digs"] * 6)
        })

    # --- CLEAN PLAYER (LOW ERRORS) ---
    if stats["avg_errors"] is not None and stats["avg_errors"] <= 2:
        tags.append({
            "name": "Consistent Player",
            "score": 90 - (stats["avg_errors"] * 10)
        })

    return tags