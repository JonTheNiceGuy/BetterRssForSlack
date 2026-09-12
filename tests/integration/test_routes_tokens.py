import datetime as dt

from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory
from app.models.user_cache import UserCache


def make_test_config(db_url):
    return Config(
        database_url=db_url,
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        token_max_ttl_seconds=7200,
    )


def build_app(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(config, make_session_factory(db_engine), scheduler=object(), slack_client=object())
    app.secret_key = "test-secret"
    return app


def login_as(client, sub):
    with client.session_transaction() as sess:
        sess["sub"] = sub
        sess["groups"] = []


def seed_user(db_engine, sub):
    session = make_session_factory(db_engine)()
    session.add(UserCache(id=sub, display_name=sub, email=f"{sub}@example.com", last_refreshed_at=dt.datetime.now(dt.timezone.utc)))
    session.commit()
    session.close()


def test_mint_requires_login(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    resp = app.test_client().post("/api/v1/tokens", json={})
    assert resp.status_code == 401


def test_mint_clamps_ttl_to_configured_max(db_engine, postgres_container):
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    resp = client.post("/api/v1/tokens", json={"ttl_seconds": 999999, "description": "cli"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert "token" in body
    assert body["description"] == "cli"


def test_list_shows_only_own_tokens_without_hash(db_engine, postgres_container):
    seed_user(db_engine, "u1")
    seed_user(db_engine, "u2")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    client.post("/api/v1/tokens", json={"ttl_seconds": 3600})

    login_as(client, "u2")
    client.post("/api/v1/tokens", json={"ttl_seconds": 3600})

    login_as(client, "u1")
    resp = client.get("/api/v1/tokens")
    body = resp.get_json()
    assert len(body) == 1
    assert "token_hash" not in body[0]
    assert "token" not in body[0]


def test_revoke_own_token(db_engine, postgres_container):
    seed_user(db_engine, "u1")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    mint_resp = client.post("/api/v1/tokens", json={"ttl_seconds": 3600})
    token_id = mint_resp.get_json()["id"]

    resp = client.post(f"/api/v1/tokens/{token_id}/revoke")
    assert resp.status_code == 200


def test_cannot_revoke_others_token(db_engine, postgres_container):
    seed_user(db_engine, "u1")
    seed_user(db_engine, "u2")
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    login_as(client, "u1")
    mint_resp = client.post("/api/v1/tokens", json={"ttl_seconds": 3600})
    token_id = mint_resp.get_json()["id"]

    login_as(client, "u2")
    resp = client.post(f"/api/v1/tokens/{token_id}/revoke")
    assert resp.status_code == 404
