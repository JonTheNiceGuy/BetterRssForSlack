import datetime as dt

from app.models.user_cache import UserCache


def upsert_user(session, *, sub: str, display_name: str | None,
                  email: str | None) -> UserCache:
    row = session.get(UserCache, sub)
    now = dt.datetime.now(dt.timezone.utc)
    if row is None:
        row = UserCache(
            id=sub, display_name=display_name, email=email, last_refreshed_at=now
        )
        session.add(row)
    else:
        row.display_name = display_name
        row.email = email
        row.last_refreshed_at = now
    session.flush()
    return row
