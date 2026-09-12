import datetime as dt

from app.identity_resolver import identity_from_session, identity_from_token
from app.acl import Identity


def test_identity_from_session_builds_identity_with_admin_flag():
    identity = identity_from_session(
        {"sub": "u1", "groups": ["eng", "admins"]}, admin_groups=frozenset({"admins"})
    )
    assert identity == Identity(sub="u1", groups=frozenset({"eng", "admins"}), is_admin=True)


def test_identity_from_session_non_admin():
    identity = identity_from_session(
        {"sub": "u1", "groups": ["eng"]}, admin_groups=frozenset({"admins"})
    )
    assert identity.is_admin is False


def test_identity_from_session_missing_sub_returns_none():
    assert identity_from_session({}, admin_groups=frozenset()) is None


def test_identity_from_token_resolves_via_verify_and_groups_lookup(db_session):
    from app.models.user_cache import UserCache
    from app.tokens.service import mint_token

    db_session.add(UserCache(id="u1", display_name="U", email="u@x.com", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    db_session.commit()
    _, raw = mint_token(db_session, "u1", ttl_seconds=3600)
    db_session.commit()

    identity = identity_from_token(
        db_session, raw, admin_groups=frozenset({"admins"}),
        groups_for_sub=lambda sub: frozenset({"eng"}),
    )
    assert identity == Identity(sub="u1", groups=frozenset({"eng"}), is_admin=False)


def test_identity_from_token_invalid_returns_none(db_session):
    identity = identity_from_token(
        db_session, "bogus", admin_groups=frozenset(), groups_for_sub=lambda sub: frozenset()
    )
    assert identity is None
