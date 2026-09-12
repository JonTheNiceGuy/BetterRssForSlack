from dataclasses import dataclass

from app.models.enums import Visibility


@dataclass(frozen=True)
class Identity:
    sub: str
    groups: frozenset[str]
    is_admin: bool


def can_access(watch, identity: Identity) -> bool:
    if identity.is_admin:
        return True
    if watch.created_by_sub == identity.sub:
        return True
    if watch.visibility == Visibility.GROUP and watch.owning_group in identity.groups:
        return True
    if watch.visibility == Visibility.PUBLIC:
        return True
    return False
