from flask import Blueprint, current_app, jsonify, request

from app.web.routes_watches import _current_identity
from app.tokens.service import mint_token, verify_token, revoke_token
from app.models.api_token import ApiToken

tokens_bp = Blueprint("tokens", __name__, url_prefix="/api/v1/tokens")


def _serialize(token: ApiToken, *, include_plaintext: str | None = None) -> dict:
    body = {
        "id": token.id,
        "description": token.description,
        "created_at": token.created_at.isoformat(),
        "expires_at": token.expires_at.isoformat(),
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
    }
    if include_plaintext is not None:
        body["token"] = include_plaintext
    return body


@tokens_bp.route("", methods=["POST"])
def mint():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    config = current_app.extensions["config"]
    body = request.get_json(force=True)
    ttl_seconds = min(body.get("ttl_seconds", config.token_max_ttl_seconds), config.token_max_ttl_seconds)
    db_session = current_app.extensions["session_factory"]()
    try:
        token, raw = mint_token(
            db_session, identity.sub, ttl_seconds=ttl_seconds, description=body.get("description")
        )
        db_session.commit()
        return jsonify(_serialize(token, include_plaintext=raw)), 201
    finally:
        db_session.close()


@tokens_bp.route("", methods=["GET"])
def index():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        rows = db_session.query(ApiToken).filter(ApiToken.owner_sub == identity.sub).all()
        return jsonify([_serialize(r) for r in rows]), 200
    finally:
        db_session.close()


@tokens_bp.route("/<int:token_id>/revoke", methods=["POST"])
def revoke(token_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        row = db_session.get(ApiToken, token_id)
        if row is None or row.owner_sub != identity.sub:
            return jsonify({"error": "not found"}), 404
        revoke_token(db_session, token_id)
        db_session.commit()
        return jsonify(_serialize(row)), 200
    finally:
        db_session.close()
