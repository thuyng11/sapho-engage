def calculate_overall_score(
    relevance_score: float,
    activity_score: float,
    engagement_score: float,
    credibility_score: float,
) -> float:
    scores = {
        "relevance_score": relevance_score,
        "activity_score": activity_score,
        "engagement_score": engagement_score,
        "credibility_score": credibility_score,
    }
    for name, score in scores.items():
        if not 0 <= score <= 100:
            raise ValueError(f"{name} must be between 0 and 100")
    return round(
        0.40 * relevance_score
        + 0.20 * activity_score
        + 0.20 * engagement_score
        + 0.20 * credibility_score,
        1,
    )
