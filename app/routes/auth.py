import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user, login_required
from ..extensions import db
from ..models import User
from ..utils import audit

bp = Blueprint("auth", __name__)
_echecs = {}


@bp.route("/installation", methods=["GET", "POST"])
def installation():
    """Premier lancement en ligne : création du compte administrateur (uniquement si aucun utilisateur n'existe)."""
    if User.query.count():
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        pwd = request.form.get("password") or ""
        if not nom or "@" not in email:
            flash("Nom et e-mail valides obligatoires.", "error")
        elif len(pwd) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
        elif pwd != request.form.get("confirmation"):
            flash("La confirmation ne correspond pas.", "error")
        else:
            u = User(nom=nom, email=email, role="admin")
            u.set_password(pwd)
            db.session.add(u)
            db.session.commit()
            login_user(u)
            audit("installation", "user", u.id)
            db.session.commit()
            flash("Compte administrateur créé. Bienvenue !", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("installation.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    from ..utils import get_param
    if get_param("connexion_obligatoire", "0") != "1":
        return redirect(url_for("main.dashboard"))
    if not User.query.count():
        return redirect(url_for("auth.installation"))
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
