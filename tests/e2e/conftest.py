import threading

import pytest
from werkzeug.serving import make_server

from app.config import Config
from app.db import make_session_factory
from app.web.app_factory import create_app

LIVE_PORT = 8765


@pytest.fixture()
def live_app(db_engine, postgres_container):
    config = Config(
        database_url=postgres_container.get_connection_url(),
        oidc_issuer="https://tinyoidc.authenti-kate.org",
        oidc_client_id="client_id_12decaf34bad56",
        oidc_client_secret="Super-+Secret_=Key0123456789",
        slack_bot_token="xoxb-fake",
        admin_oidc_groups=frozenset({"admins"}),
    )
    app = create_app(config, make_session_factory(db_engine), scheduler=object(), slack_client=object())
    app.secret_key = "e2e-secret"

    server = make_server("127.0.0.1", LIVE_PORT, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{LIVE_PORT}"
    finally:
        server.shutdown()
        thread.join()
