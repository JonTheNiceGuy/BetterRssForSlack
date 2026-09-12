import datetime as dt

from app.models.user_cache import UserCache
from app.tokens.service import mint_token, verify_token, revoke_token


def make_user(db_session, sub="user-1"):
    user = UserCache(
        id=sub, display_name="Test User", email="t@example.com",
        last_refreshed_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_mint_then_verify_round_trip(db_session):
    make_user(db_session)
    token_row, raw = mint_token(db_session, "user-1", ttl_seconds=3600, description="cli")
    db_session.commit()

    found = verify_token(db_session, raw)
    assert found is not None
    assert found.id == token_row.id
    assert found.owner_sub == "user-1"
    assert found.description == "cli"


def test_verify_rejects_wrong_token(db_session):
    make_user(db_session)
    mint_token(db_session, "user-1", ttl_seconds=3600)
    db_session.commit()
    assert verify_token(db_session, "not-a-real-token") is None


def test_verify_rejects_expired_token(db_session):
    make_user(db_session)
    token_row, raw = mint_token(db_session, "user-1", ttl_seconds=-1)
    db_session.commit()
    assert verify_token(db_session, raw) is None


def test_verify_rejects_revoked_token(db_session):
    make_user(db_session)
    token_row, raw = mint_token(db_session, "user-1", ttl_seconds=3600)
    db_session.commit()
    revoke_token(db_session, token_row.id)
    db_session.commit()
    assert verify_token(db_session, raw) is None
