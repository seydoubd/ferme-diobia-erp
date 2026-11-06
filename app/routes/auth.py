import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user, login_required
from ..extensions import db
from ..models import User
from ..utils import audit

bp = Blueprint("auth", __name__)
_echecs = {}


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        cle = f"{ip}:{email}"
        n, t0 = _echecs.get(cle, (0, 0))
        if n >= 5 and time.time() - t0 < 300:
            flash("Trop de tentatives. Réessayez dans 5 minutes.", "error")
            return render_template("login.html"), 429
        u = User.query.filter(db.func.lower(User.email) == email).first()
        if u and u.actif and u.check_password(request.form.get("password", "")):
            _echecs.pop(cle, None)
            login_user(u, remember=bool(request.form.get("remember")))
            session.permanent = True
            audit("connexion", "user", u.id)
            db.session.commit()
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        _echecs[cle] = (n + 1, time.time())
        flash("Identifiants incorrects.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    if current_user.is_authenticated:
        audit("deconnexion", "user", current_user.id)
        db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("actuel", "")):
            flash("Mot de passe actuel incorrect.", "error")
        elif len(request.form.get("nouveau", "")) < 8:
            flash("Le nouveau mot de passe doit contenir au moins 8 caractères.", "error")
        elif request.form.get("nouveau") != request.form.get("confirmation"):
            flash("La confirmation ne correspond pas.", "error")
        else:
            current_user.set_password(request.form["nouveau"])
            audit("changement_mdp", "user", current_user.id)
            db.session.commit()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("profil.html")
