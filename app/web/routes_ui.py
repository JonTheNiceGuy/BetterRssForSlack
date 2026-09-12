import datetime as dt

from flask import Blueprint, current_app, flash, redirect, render_template, request

from app.acl import can_access
from app.web.routes_watches import _current_identity
from app.models.rss_watch import RssWatch
from app.models.enums import Template, Visibility
from app.models.api_token import ApiToken
from app.worker.scheduler import sync_job, pause_job, resume_job, remove_job
from app.slack.channel_cache import list_bot_channels
from app.tokens.service import mint_token, revoke_token

ui_bp = Blueprint("ui", __name__)


@ui_bp.route("/")
def dashboard():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        all_watches = db_session.query(RssWatch).all()
        visible = [w for w in all_watches if can_access(w, identity)]
        return render_template("dashboard.html", watches=visible, identity=identity)
    finally:
        db_session.close()


@ui_bp.route("/watches/new", methods=["GET", "POST"])
def new_watch():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    slack_client = current_app.extensions["slack_client"]

    if request.method == "GET":
        channels = list_bot_channels(slack_client)
        return render_template("watch_form.html", channels=channels, identity=identity)

    db_session = current_app.extensions["session_factory"]()
    try:
        watch = RssWatch(
            feed_url=request.form["feed_url"],
            slack_channel_id=request.form["slack_channel_id"],
            template=Template(request.form["template"]),
            visibility=Visibility(request.form.get("visibility", "owner_only")),
            owning_group=request.form.get("owning_group") or None,
            created_by_sub=identity.sub,
            created_at=dt.datetime.now(dt.timezone.utc),
            check_interval_seconds=int(request.form["check_interval_seconds"]),
            auto_pause_after_failures=int(request.form.get("auto_pause_after_failures", 0)),
        )
        db_session.add(watch)
        db_session.commit()
        sync_job(current_app.extensions["scheduler"], watch)
        flash("Watch created.")
        return redirect("/")
    finally:
        db_session.close()


def _load_visible_watch(db_session, identity, watch_id):
    watch = db_session.get(RssWatch, watch_id)
    if watch is None or not can_access(watch, identity):
        return None
    return watch


@ui_bp.route("/watches/<int:watch_id>")
def watch_detail(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        return render_template("watch_detail.html", watch=watch, identity=identity)
    finally:
        db_session.close()


@ui_bp.route("/watches/<int:watch_id>/delete", methods=["POST"])
def delete_watch(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        db_session.delete(watch)
        db_session.commit()
        remove_job(current_app.extensions["scheduler"], watch_id)
        flash("Watch deleted.")
        return redirect("/")
    finally:
        db_session.close()


@ui_bp.route("/watches/<int:watch_id>/pause", methods=["POST"])
def pause_watch(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        watch.is_active = False
        db_session.commit()
        pause_job(current_app.extensions["scheduler"], watch_id)
        return redirect("/")
    finally:
        db_session.close()


@ui_bp.route("/watches/<int:watch_id>/resume", methods=["POST"])
def resume_watch(watch_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        watch = _load_visible_watch(db_session, identity, watch_id)
        if watch is None:
            return "Not found", 404
        watch.is_active = True
        watch.consecutive_failure_count = 0
        db_session.commit()
        resume_job(current_app.extensions["scheduler"], watch_id)
        return redirect("/")
    finally:
        db_session.close()


@ui_bp.route("/tokens", methods=["GET"])
def tokens_page():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        rows = db_session.query(ApiToken).filter(ApiToken.owner_sub == identity.sub).all()
        return render_template("tokens.html", tokens=rows, identity=identity, minted_plaintext=None)
    finally:
        db_session.close()


@ui_bp.route("/tokens/mint", methods=["POST"])
def tokens_mint():
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    config = current_app.extensions["config"]
    ttl_seconds = min(int(request.form.get("ttl_seconds", config.token_max_ttl_seconds)), config.token_max_ttl_seconds)
    db_session = current_app.extensions["session_factory"]()
    try:
        _, raw = mint_token(
            db_session, identity.sub, ttl_seconds=ttl_seconds,
            description=request.form.get("description") or None,
        )
        db_session.commit()
        rows = db_session.query(ApiToken).filter(ApiToken.owner_sub == identity.sub).all()
        return render_template("tokens.html", tokens=rows, identity=identity, minted_plaintext=raw)
    finally:
        db_session.close()


@ui_bp.route("/tokens/<int:token_id>/revoke", methods=["POST"])
def tokens_revoke(token_id):
    identity = _current_identity()
    if identity is None:
        return redirect("/login")
    db_session = current_app.extensions["session_factory"]()
    try:
        row = db_session.get(ApiToken, token_id)
        if row is None or row.owner_sub != identity.sub:
            return "Not found", 404
        revoke_token(db_session, token_id)
        db_session.commit()
        return redirect("/tokens")
    finally:
        db_session.close()
