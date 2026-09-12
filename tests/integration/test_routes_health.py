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
    )


def test_healthz_returns_ok(db_engine, postgres_container):
    config = make_test_config(postgres_container.get_connection_url())
    app = create_app(config, make_session_factory(db_engine), scheduler=object(), slack_client=object())
    client = app.test_client()

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_healthz_returns_503_when_db_unreachable(postgres_container):
    config = make_test_config("postgresql+psycopg://bad:bad@localhost:1/nope")
    from sqlalchemy import create_engine
    bad_engine = create_engine(config.database_url, future=True)
    app = create_app(config, make_session_factory(bad_engine), scheduler=object(), slack_client=object())
    client = app.test_client()

    resp = client.get("/healthz")
    assert resp.status_code == 503
