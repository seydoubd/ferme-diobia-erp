import io
import json
import os
from datetime import datetime
from flask import Blueprint, render_template, request, url_for, send_file, flash, redirect, current_app
from flask_login import login_required, current_user
from ..extensions import db
from ..models import User, Parametre
from ..permissions import require, ROLES, PERMISSIONS
from ..utils import BusinessError, get_param, set_param, audit, to_float
from ..demo import nb_demo, purger_demo
from . import run_action

bp = Blueprint("admin", __name__)
PARAMS_EDITABLES = [("nom_entreprise", "Nom de l'entreprise"), ("slogan", "Slogan"), ("adresse", "Adresse"),
                    ("telephone", "Téléphone"), ("email", "E-mail"), ("ninea", "NINEA"), ("rccm", "RCCM"),
                    ("mentions_facture", "Mentions en bas de facture")]


@bp.before_request
@login_required
def _auth():
    pass


@bp.route("/parametres", methods=["GET", "POST"])
@require("parametres.gerer")
def parametres():
    if request.method == "POST":
        f = request.form

        def go():
            for k, _ in PARAMS_EDITABLES:
                if k in f:
                    set_param(k, f.get(k, "").strip())
            set_param("tva_active", "1" if f.get("tva_active") else "0")
            taux = to_float(f.get("tva_taux"), 18)
            if not 0 <= taux <= 100:
                raise BusinessError("Taux de TVA invalide.")
            set_param("tva_taux", taux)
            set_param("taux_is", to_float(f.get("taux_is"), 30))
            set_param("compta_validation_auto", "1" if f.get("compta_validation_auto") else "0")
            set_param("alerte_peremption_jours", int(to_float(f.get("alerte_peremption_jours"), 2)))
            audit("parametres", "parametre", None, {k: f.get(k) for k in f if k not in ("csrf_token", "_ft")})
            return True
        return run_action(go, "Paramètres enregistrés.", url_for("admin.parametres"))
    params = {p.cle: p.valeur for p in Parametre.query.all()}
    return render_template("admin/parametres.html", params=params, PARAMS=PARAMS_EDITABLES, demo=nb_demo())


@bp.route("/parametres/demo", methods=["POST"])
@require("parametres.gerer")
def demo():
    action = request.form.get("action")
    if action == "supprimer":
        try:
            purger_demo()
            set_param("demo_chargee", "0")
            audit("purge_demo", "parametre", None)
            db.session.commit()
            flash("Données de démonstration supprimées. L'application est prête pour vos données réelles.", "success")
        except RuntimeError as e:
            flash(str(e), "error")
    elif action == "charger":
        from ..seed import seed_demo
        if nb_demo()["ventes"]:
            flash("Les données de démonstration sont déjà chargées.", "warning")
        else:
            try:
                seed_demo()
                flash("Données de démonstration chargées.", "success")
            except BusinessError as e:
                db.session.rollback()
                flash(f"Chargement impossible : {e}", "error")
    return redirect(url_for("admin.parametres"))


@bp.route("/parametres/sauvegarde")
@require("parametres.gerer")
def sauvegarde():
    """Sauvegarde : copie de la base SQLite, ou export JSON complet (PostgreSQL)."""
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    audit("sauvegarde", "parametre", None)
    db.session.commit()
    if uri.startswith("sqlite:///"):
        import sqlite3
        src = sqlite3.connect(uri.replace("sqlite:///", ""))
        tmp = os.path.join(current_app.instance_path, f"backup_{stamp}.db")
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close()
        src.close()
        data = open(tmp, "rb").read()
        os.remove(tmp)
        return send_file(io.BytesIO(data), as_attachment=True, download_name=f"ferme_diobia_{stamp}.db")
    out = {}
    for t in db.metadata.sorted_tables:
        rows = db.session.execute(t.select()).mappings().all()
        out[t.name] = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]
    buf = io.BytesIO(json.dumps(out, ensure_ascii=False, indent=1).encode())
    return send_file(buf, as_attachment=True, download_name=f"ferme_diobia_{stamp}.json", mimetype="application/json")


@bp.route("/utilisateurs", methods=["GET", "POST"])
@require("users.gerer")
def utilisateurs():
    if request.method == "POST":
        f = request.form

        def go():
            u = db.session.get(User, f.get("id", type=int)) if f.get("id") else User()
            u.nom = (f.get("nom") or "").strip()
            u.email = (f.get("email") or "").strip().lower()
            if not u.nom or "@" not in u.email:
                raise BusinessError("Nom et e-mail valides obligatoires.")
            if User.query.filter(User.email == u.email, User.id != (u.id or 0)).first():
                raise BusinessError("Cet e-mail est déjà utilisé.")
            if f.get("role") not in ROLES:
                raise BusinessError("Rôle invalide.")
            if u.id == current_user.id and f.get("role") != "admin":
                raise BusinessError("Vous ne pouvez pas retirer votre propre rôle d'administrateur.")
            u.role = f["role"]
            u.permissions_extra = ",".join(p for p in f.getlist("perms") if p in PERMISSIONS)
            u.actif = f.get("actif", "1") == "1" or u.id == current_user.id
            if f.get("password"):
                if len(f["password"]) < 8:
                    raise BusinessError("Mot de passe : 8 caractères minimum.")
                u.set_password(f["password"])
            elif not u.id:
                raise BusinessError("Mot de passe obligatoire pour un nouvel utilisateur.")
            db.session.add(u)
            db.session.flush()
            audit("utilisateur", "user", u.id, {"email": u.email, "role": u.role})
            return u
        return run_action(go, "Utilisateur enregistré.", url_for("admin.utilisateurs"))
    return render_template("admin/utilisateurs.html", users=User.query.order_by(User.nom).all(), ROLES=ROLES,
                           PERMISSIONS=PERMISSIONS)
