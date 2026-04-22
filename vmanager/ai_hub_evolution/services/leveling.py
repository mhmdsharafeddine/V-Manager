def calculate_player_level(member):
    from .stat_analysis import get_player_aggregated_stats

    stats = get_player_aggregated_stats(member)

    # Simple weighted score
    score = 0

    score += (stats["avg_kills"] or 0) * 2
    score += (stats["avg_aces"] or 0) * 3
    score += (stats["avg_blocks"] or 0) * 2.5
    score += (stats["avg_assists"] or 0) * 2
    score += (stats["avg_digs"] or 0) * 1.5
    score -= (stats["avg_errors"] or 0) * 2

    # Normalize
    score = max(0, score)

    # LEVEL SYSTEM (every 50 points = new level)
    level = int(score // 50)
    progress = (score % 50) / 50 * 100

    return {
        "level": level,
        "progress_to_next": round(progress, 2),
        "raw_score": round(score, 2)
    }