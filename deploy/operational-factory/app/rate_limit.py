from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from app.settings import RATE_WINDOW_SECONDS


def enforce_rate_limit(session, *, actor_id: str, action_name: str, max_frequency: int) -> bool:
    since = datetime.now(timezone.utc) - timedelta(seconds=RATE_WINDOW_SECONDS)
    count = session.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM audit_log
            WHERE actor_id = :actor_id
              AND action = :action
              AND created_at >= :since
            """
        ),
        {"actor_id": actor_id, "action": action_name, "since": since},
    ).scalar_one()
    return count < max_frequency
