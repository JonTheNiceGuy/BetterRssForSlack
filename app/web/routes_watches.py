import datetime as dt

from flask import Blueprint, current_app, jsonify, request, session

from app.acl import Identity, can_access
from app.identity_resolver import identity_from_session
from app.models.rss_watch import RssWatch
from app.models.enums import Template, Visibility
from app.worker.scheduler import sync_job, pause_job, resume_job, remove_job
from app.slack.channel_cache import list_bot_channels

watches_bp = Blueprint("watches", __name__, url_prefix="/api/v1/watches")


def _current_identity() -> Identity | None:
    config = current_app.extensions["config"]
    return identity_from_session(dict(session), config.admin_oidc_groups)


def _serialize(watch: RssWatch) -> dict:
    return {
        "id": watch.id,
        "feed_url": watch.feed_url,
        "slack_channel_id": watch.slack_channel_id,
        "template": watch.template.value,
        "visibility": watch.visibility.value,
        "owning_group": watch.owning_group,
        "created_by_sub": watch.created_by_sub,
        "check_interval_seconds": watch.check_interval_seconds,
        "is_active": watch.is_active,
        "auto_pause_after_failures": watch.auto_pause_after_failures,
        "consecutive_failure_count": watch.consecutive_failure_count,
        "last_checked_at": watch.last_checked_at.isoformat() if watch.last_checked_at else None,
        "last_posted_at": watch.last_posted_at.isoformat() if watch.last_posted_at else None,
        "last_error": watch.last_error,
    }


@watches_bp.route("", methods=["GET"])
def index():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        all_watches = db_session.query(RssWatch).all()
        visible = [w for w in all_watches if can_access(w, identity)]
        return jsonify([_serialize(w) for w in visible]), 200
    finally:
        db_session.close()


@watches_bp.route("", methods=["POST"])
def create():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True)
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = RssWatch(
            feed_url=body["feed_url"],
            slack_channel_id=body["slack_channel_id"],
            template=Template(body["template"]),
            visibility=Visibility(body.get("visibility", "owner_only")),
            owning_group=body.get("owning_group"),
            created_by_sub=identity.sub,
            created_at=dt.datetime.now(dt.timezone.utc),
            check_interval_seconds=body["check_interval_seconds"],
            auto_pause_after_failures=body.get("auto_pause_after_failures", 0),
        )
        db_session.add(watch)
        db_session.commit()
        sync_job(current_app.extensions["scheduler"], watch)
        return jsonify(_serialize(watch)), 201
    finally:
        db_session.close()


def _load_visible_watch(db_session, identity, watch_id):
    watch = db_session.get(RssWatch, watch_id)
    if watch is None or not can_access(watch, identity):
        return None
    return watch


@watches_bp.route("/<int:watch_id>", methods=["GET"])
def detail(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()


@watches_bp.route("/<int:watch_id>", methods=["PATCH"])
def edit(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        body = request.get_json(force=True)
        for field in ("feed_url", "slack_channel_id", "check_interval_seconds",
                        "owning_group", "auto_pause_after_failures"):
            if field in body:
                setattr(watch, field, body[field])
        if "template" in body:
            watch.template = Template(body["template"])
        if "visibility" in body:
            watch.visibility = Visibility(body["visibility"])
        db_session.commit()
        sync_job(current_app.extensions["scheduler"], watch)
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()


@watches_bp.route("/<int:watch_id>", methods=["DELETE"])
def delete(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        db_session.delete(watch)
        db_session.commit()
        remove_job(current_app.extensions["scheduler"], watch_id)
        return "", 204
    finally:
        db_session.close()


@watches_bp.route("/<int:watch_id>/pause", methods=["POST"])
def pause(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        watch.is_active = False
        db_session.commit()
        pause_job(current_app.extensions["scheduler"], watch_id)
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()


@watches_bp.route("/<int:watch_id>/resume", methods=["POST"])
def resume(watch_id):
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return jsonify({"error": "not found"}), 404
        watch.is_active = True
        watch.consecutive_failure_count = 0
        db_session.commit()
        resume_job(current_app.extensions["scheduler"], watch_id)
        return jsonify(_serialize(watch)), 200
    finally:
        db_session.close()


def slack_channels_view():
    identity = _current_identity()
    if identity is None:
        return jsonify({"error": "unauthorized"}), 401
    slack_client = current_app.extensions["slack_client"]
    return jsonify(list_bot_channels(slack_client)), 200
