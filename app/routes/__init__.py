"""Enregistrement des blueprints et utilitaires de routes."""
from datetime import date, datetime, timedelta
from flask import request, jsonify, flash, redirect
from ..extensions import db
from ..utils import BusinessError, consume_form_token, new_form_token, to_float, to_date


def wants_json():
    return request.headers.get("X-Requested-With") == "fetch"


def run_action(fn, success, redirect_to, error_to=None):
    """Exécute une opération métier dans UNE transaction : tout est validé, ou rien.
    - jeton de formulaire consommé (anti double soumission) ;
    - BusinessError → rollback complet, message clair."""
    try:
        if not consume_form_token(request.form.get("_ft")):
            db.session.rollback()
            msg = "Ce formulaire a déjà été envoyé : l'opération n'a pas été enregistrée une seconde fois."
            if wants_json():
                return jsonify(ok=False, error=msg, form_token=new_form_token())
            flash(msg, "warning")
            return redirect(error_to or request.referrer or redirect_to)
        res = fn()
        db.session.commit()
        msg = success(res) if callable(success) else success
        url = redirect_to(res) if callable(redirect_to) else redirect_to
        if wants_json():
            return jsonify(ok=True, message=msg, redirect=url)
        flash(msg, "success")
        return redirect(url)
    except BusinessError as e:
        db.session.rollback()
        if wants_json():
            return jsonify(ok=False, error=str(e), form_token=new_form_token())
        flash(str(e), "error")
        return redirect(error_to or request.referrer or (redirect_to if not callable(redirect_to) else "/"))


def lignes_form(*fields, required="produit_id"):
    lists = {f: request.form.getlist(f + "[]") for f in fields}
    n = max((len(v) for v in lists.values()), default=0)
    out = []
    for i in range(n):
        row = {f: (lists[f][i] if i < len(lists[f]) else "") for f in fields}
        if row.get(required):
            out.append(row)
    return out


def periode_args(defaut="mois"):
    today = date.today()
    if defaut == "mois":
        d0 = today.replace(day=1)
    elif defaut == "30j":
        d0 = today - timedelta(days=29)
    else:
        d0 = date(today.year, 1, 1)
    debut = to_date(request.args.get("debut"), d0)
    fin = to_date(request.args.get("fin"), today)
    return debut, fin


def register_blueprints(app):
    from . import auth, main, produits, stocks, achats, transformations, ventes, tiers, finances, compta, rh, rapports, admin
    for mod in (auth, main, produits, stocks, achats, transformations, ventes, tiers, finances, compta, rh, rapports, admin):
        app.register_blueprint(mod.bp)
