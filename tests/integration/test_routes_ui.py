import datetime as dt

from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory
from app.models.slack_channel_cache import SlackChannelCache
from app.models.user_cache import UserCache
import app.web.routes_ui as routes_ui


class FakeSlackClient:
    def conversations_list(self, **kwargs):
        return {"channels": [{"id": "C1", "name": "general"}]}


def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        admin_oidc_groups=frozenset({"admins"}),
    )


def build_app(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(
        config, make_session_factory(db_engine), scheduler=object(), slack_client=FakeSlackClient()
    )
    app.secret_key = "test-secret"
    return app


def login_as(client, sub, groups=None):
    with client.session_transaction() as sess:
        sess["sub"] = sub
        sess["groups"] = groups or []


def seed_channel(db_engine):
    session = make_session_factory(db_engine)()
    session.add(SlackChannelCache(id="C1", name="general", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    session.commit()
    session.close()


def seed_user(db_engine, sub):
    session = make_session_factory(db_engine)()
    session.add(UserCache(id=sub, display_name=sub, email=f"{sub}@example.com", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    session.commit()
    session.close()


def test_dashboard_requires_login_redirects_to_login(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    resp = app.test_client().get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")


def test_new_watch_form_shows_channel_picker_options(db_engine, postgres_container):
    seed_channel(db_engine)
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.get("/watches/new")
    assert resp.status_code == 200
    assert b"general" in resp.data


def test_create_watch_via_form_then_see_it_on_dashboard(db_engine, postgres_container, monkeypatch):
    monkeypatch.setattr(routes_ui, "sync_job", lambda scheduler, watch: None)
    seed_channel(db_engine)
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/watches/new", data={
        "feed_url": "https://example.com/feed.xml",
        "slack_channel_id": "C1",
        "template": "headline_link",
        "visibility": "owner_only",
        "check_interval_seconds": "3600",
        "auto_pause_after_failures": "0",
    }, follow_redirects=False)
    assert resp.status_code == 302

    resp = client.get("/")
    assert b"https://example.com/feed.xml" in resp.data


def test_admin_badge_shown_for_admin_group_member(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1", groups=["admins"])
    resp = client.get("/")
    assert b"(admin)" in resp.data


def test_mint_token_via_form_shows_plaintext_once(db_engine, postgres_container):
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/tokens/mint", data={"ttl_seconds": "3600", "description": "cli"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"cli" in resp.data
