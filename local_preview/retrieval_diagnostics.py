"""Failure accounting shared by retrieval composition and offline evaluations."""


def has_retrieval_failure(details: dict) -> bool:
    """Keep failed stages and earlier agentic attempts visible after fallback."""
    return (
        details.get("status") in {"error", "degraded"}
        or any(
            stage.get("status") in {"error", "degraded"}
            for stage in (details.get("stages") or {}).values()
        )
        or any(
            has_retrieval_failure(attempt)
            for attempt in (
                (details.get("agentic_retrieval") or {}).get("attempts") or []
            )
        )
    )
