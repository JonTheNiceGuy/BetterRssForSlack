from unittest.mock import patch

from app.config import Config
from app.web.app_factory import create_app
from app.db import make_session_factory


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
    app = create_app(config, make_session_factory(db_engine))
    app.secret_key = "test-secret"
    return app


def test_login_redirects_to_provider(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    with patch.object(
        app.extensions["oauth"].tinyoidc, "authorize_redirect",
        return_value=("redirect", 302),
    ) as mock_redirect:
        client.get("/login")
        assert mock_redirect.called


def test_callback_upserts_user_and_sets_session(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    fake_token = {
        "userinfo": {
            "sub": "u1", "name": "Jon", "email": "jon@example.com", "groups": ["admins"],
        }
    }
    with patch.object(
        app.extensions["oauth"].tinyoidc, "authorize_access_token", return_value=fake_token,
    ):
        resp = client.get("/auth/callback")
        assert resp.status_code == 302

    with client.session_transaction() as sess:
        assert sess["sub"] == "u1"
        assert sess["groups"] == ["admins"]

    from app.models.user_cache import UserCache
    session = make_session_factory(db_engine)()
    stored = session.get(UserCache, "u1")
    assert stored.display_name == "Jon"
    session.close()


def test_logout_clears_session(db_engine, postgres_container):
    app = build_app(db_engine, postgres_container)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["sub"] = "u1"
    client.get("/logout")
    with client.session_transaction() as sess:
        assert "sub" not in sess
