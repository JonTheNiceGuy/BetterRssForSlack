from types import SimpleNamespace

from app.acl import Identity, can_access
from app.models.enums import Visibility


def watch(**overrides):
    defaults = dict(
        created_by_sub="owner-sub",
        visibility=Visibility.OWNER_ONLY,
        owning_group=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_admin_can_access_anything():
    identity = Identity(sub="someone-else", groups=frozenset(), is_admin=True)
    assert can_access(watch(), identity) is True


def test_owner_can_access_own_owner_only_watch():
    identity = Identity(sub="owner-sub", groups=frozenset(), is_admin=False)
    assert can_access(watch(), identity) is True


def test_non_owner_cannot_access_owner_only_watch():
    identity = Identity(sub="stranger", groups=frozenset(), is_admin=False)
    assert can_access(watch(), identity) is False


def test_group_member_can_access_group_watch():
    identity = Identity(sub="stranger", groups=frozenset({"eng"}), is_admin=False)
    w = watch(visibility=Visibility.GROUP, owning_group="eng")
    assert can_access(w, identity) is True


def test_non_group_member_cannot_access_group_watch():
    identity = Identity(sub="stranger", groups=frozenset({"sales"}), is_admin=False)
    w = watch(visibility=Visibility.GROUP, owning_group="eng")
    assert can_access(w, identity) is False


def test_anyone_authenticated_can_access_public_watch():
    identity = Identity(sub="stranger", groups=frozenset(), is_admin=False)
    w = watch(visibility=Visibility.PUBLIC)
    assert can_access(w, identity) is True
