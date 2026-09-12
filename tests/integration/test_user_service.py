from app.users.service import upsert_user
from app.models.user_cache import UserCache


def test_upsert_creates_new_user(db_session):
    user = upsert_user(db_session, sub="sub-1", display_name="Jon", email="jon@example.com")
    db_session.commit()
    assert user.id == "sub-1"
    assert db_session.get(UserCache, "sub-1").display_name == "Jon"


def test_upsert_updates_existing_user(db_session):
    upsert_user(db_session, sub="sub-1", display_name="Jon", email="jon@example.com")
    db_session.commit()

    upsert_user(db_session, sub="sub-1", display_name="Jon S", email="jon@example.com")
    db_session.commit()

    stored = db_session.get(UserCache, "sub-1")
    assert stored.display_name == "Jon S"
