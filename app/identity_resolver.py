from typing import Callable

from app.acl import Identity
from app.tokens.service import verify_token


def identity_from_session(session_data: dict, admin_groups: frozenset[str]) -> Identity | None:
    sub = session_data.get("sub")
    if not sub:
        return None
    groups = frozenset(session_data.get("groups", []))
    return Identity(sub=sub, groups=groups, is_admin=bool(groups & admin_groups))


def identity_from_token(
    db_session,
    raw_token: str,
    admin_groups: frozenset[str],
    *,
    groups_for_sub: Callable[[str], frozenset[str]],
) -> Identity | None:
    token_row = verify_token(db_session, raw_token)
    if token_row is None:
        return None
    groups = groups_for_sub(token_row.owner_sub)
    return Identity(sub=token_row.owner_sub, groups=groups, is_admin=bool(groups & admin_groups))
