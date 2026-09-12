import datetime as dt

from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory
from app.models.slack_channel_cache import SlackChannelCache
from app.models.user_cache import UserCache
import app.web.routes_watches as routes_watches


class Recorder:
    def __init__(self):
        self.synced = []
        self.paused = []
        self.resumed = []
        self.removed = []


def install_recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(routes_watches, "sync_job", lambda scheduler, watch: rec.synced.append(watch.id))
    monkeypatch.setattr(routes_watches, "pause_job", lambda scheduler, wid: rec.paused.append(wid))
    monkeypatch.setattr(routes_watches, "resume_job", lambda scheduler, wid: rec.resumed.append(wid))
    monkeypatch.setattr(routes_watches, "remove_job", lambda scheduler, wid: rec.removed.append(wid))
    return rec


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
    app = create_app(config, make_session_factory(db_engine), scheduler=object())
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


def test_create_watch_requires_login(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    resp = client.post("/api/v1/watches", json={})
    assert resp.status_code == 401


def test_create_then_list_own_watch(db_engine, postgres_container, monkeypatch):
    install_recorder(monkeypatch)
    seed_channel(db_engine)
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")

    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml",
        "slack_channel_id": "C1",
        "template": "headline_link",
        "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    assert resp.status_code == 201
    watch_id = resp.get_json()["id"]

    resp = client.get("/api/v1/watches")
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.get_json()]
    assert watch_id in ids


def test_owner_only_watch_hidden_from_other_users(db_engine, postgres_container, monkeypatch):
    install_recorder(monkeypatch)
    seed_channel(db_engine)
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml", "slack_channel_id": "C1",
        "template": "headline_link", "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    watch_id = resp.get_json()["id"]

    login_as(client, "stranger")
    resp = client.get(f"/api/v1/watches/{watch_id}")
    assert resp.status_code == 404  # hidden, not 403 -- existence not disclosed


def test_owner_can_delete_own_watch(db_engine, postgres_container, monkeypatch):
    rec = install_recorder(monkeypatch)
    seed_channel(db_engine)
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml", "slack_channel_id": "C1",
        "template": "headline_link", "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    watch_id = resp.get_json()["id"]

    resp = client.delete(f"/api/v1/watches/{watch_id}")
    assert resp.status_code == 204
    assert rec.removed == [watch_id]


def test_pause_and_resume(db_engine, postgres_container, monkeypatch):
    rec = install_recorder(monkeypatch)
    seed_channel(db_engine)
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/watches", json={
        "feed_url": "https://example.com/feed.xml", "slack_channel_id": "C1",
        "template": "headline_link", "visibility": "owner_only",
        "check_interval_seconds": 3600,
    })
    watch_id = resp.get_json()["id"]

    resp = client.post(f"/api/v1/watches/{watch_id}/pause")
    assert resp.status_code == 200
    assert rec.paused == [watch_id]

    resp = client.post(f"/api/v1/watches/{watch_id}/resume")
    assert resp.status_code == 200
    assert rec.resumed == [watch_id]
