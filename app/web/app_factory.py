from flask import Flask

from app.config import Config
from app.web.routes_health import health_bp


def create_app(config: Config, session_factory, scheduler=None, slack_client=None) -> Flask:
    app = Flask(__name__)
    app.extensions["config"] = config
    app.extensions["session_factory"] = session_factory
    app.extensions["scheduler"] = scheduler
    app.extensions["slack_client"] = slack_client

    app.register_blueprint(health_bp)
    return app
