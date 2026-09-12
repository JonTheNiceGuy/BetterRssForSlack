from authlib.integrations.flask_client import OAuth
from flask import Flask

from app.config import Config
from app.web.routes_health import health_bp
from app.web.routes_auth import auth_bp
from app.web.routes_watches import watches_bp


def create_app(config: Config, session_factory, scheduler=None, slack_client=None) -> Flask:
    app = Flask(__name__)
    app.extensions["config"] = config
    app.extensions["session_factory"] = session_factory
    app.extensions["scheduler"] = scheduler
    app.extensions["slack_client"] = slack_client
    app.secret_key = config.oidc_client_secret  # POC only

    oauth = OAuth(app)
    oauth.register(
        name="tinyoidc",
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        server_metadata_url=f"{config.oidc_issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email groups"},
    )
    app.extensions["oauth"] = oauth

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(watches_bp)
    return app
