from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

health_bp = Blueprint("health", __name__)


@health_bp.route("/healthz")
def healthz():
    session_factory = current_app.extensions["session_factory"]
    session = session_factory()
    try:
        session.execute(text("SELECT 1"))
        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 503
    finally:
        session.close()
