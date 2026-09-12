import datetime as dt
import hashlib
import secrets

from app.models.api_token import ApiToken


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def mint_token(session, owner_sub: str, *, ttl_seconds: int,
                description: str | None = None) -> tuple[ApiToken, str]:
    raw = secrets.token_urlsafe(32)
    now = dt.datetime.now(dt.timezone.utc)
    row = ApiToken(
        token_hash=_hash(raw),
        owner_sub=owner_sub,
        description=description,
        created_at=now,
        expires_at=now + dt.timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    session.flush()
    return row, raw


def verify_token(session, raw_token: str) -> ApiToken | None:
    row = (
        session.query(ApiToken)
        .filter(ApiToken.token_hash == _hash(raw_token))
        .one_or_none()
    )
    if row is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    if row.revoked_at is not None:
        return None
    if row.expires_at <= now:
        return None
    return row


def revoke_token(session, token_id: int) -> None:
    row = session.get(ApiToken, token_id)
    if row is not None:
        row.revoked_at = dt.datetime.now(dt.timezone.utc)
