from flask import Blueprint, current_app, redirect, session, url_for

from app.users.service import upsert_user

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login")
def login():
    oauth = current_app.extensions["oauth"]
    redirect_uri = url_for("auth.callback", _external=True)
    return oauth.tinyoidc.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/callback")
def callback():
    oauth = current_app.extensions["oauth"]
    token = oauth.tinyoidc.authorize_access_token()
    userinfo = token["userinfo"]
    sub = userinfo["sub"]
    groups = userinfo.get("groups", [])
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]

    session_factory = current_app.extensions["session_factory"]
    db_session = session_factory()
    try:
        upsert_user(
            db_session, sub=sub,
            display_name=userinfo.get("name"), email=userinfo.get("email"),
        )
        db_session.commit()
    finally:
        db_session.close()

    session["sub"] = sub
    session["groups"] = groups
    return redirect("/")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")
