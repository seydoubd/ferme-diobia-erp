"""Utilitaires : formats sénégalais, numérotation, audit, erreurs métier."""
import json
import secrets
from datetime import datetime, date
from flask_login import current_user
from sqlalchemy import func
from .extensions import db


class BusinessError(Exception):
    """Erreur métier : l'opération est refusée et la transaction annulée."""


EPS = 1e-6


# ── Formats (Sénégal : 1 250 000 FCFA · 12,5 kg · 06/11/2026) ──
def _group(n_str):
    neg = n_str.startswith("-")
    s = n_str.lstrip("-")
    parts = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return ("-" if neg else "") + " ".join(parts)


def fcfa(v, suffix=True):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0
    txt = _group(str(int(round(v))))
    return f"{txt} FCFA" if suffix else txt


def nombre(v, dec=2):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0
    if abs(v - round(v)) < 1e-9:
        return _group(str(int(round(v))))
    s = f"{v:.{dec}f}".rstrip("0").rstrip(".")
    ent, _, frac = s.partition(".")
    return _group(ent) + ("," + frac if frac else "")


def date_fr(d, heure=False):
    if not d:
        return ""
    if isinstance(d, str):
        return d
    if heure and isinstance(d, datetime):
        return d.strftime("%d/%m/%Y %H:%M")
    return d.strftime("%d/%m/%Y")


MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]


# ── Saisie ──
def to_float(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        raise BusinessError(f"Valeur numérique invalide : « {v} »")


def to_date(v, default=None):
    if not v:
        return default
    if isinstance(v, (date, datetime)):
        return v
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise BusinessError(f"Date invalide : « {v} »")


def to_datetime(v, default=None):
    if not v:
        return default or datetime.now()
    if isinstance(v, datetime):
        return v
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(v, fmt)
            if fmt == "%Y-%m-%d":
                n = datetime.now()
                d = d.replace(hour=n.hour, minute=n.minute)
            return d
        except ValueError:
            pass
    raise BusinessError(f"Date invalide : « {v} »")


# ── Paramètres ──
def get_param(cle, default=None):
    from .models import Parametre
    p = db.session.get(Parametre, cle)
    return p.valeur if p and p.valeur is not None else default


def set_param(cle, valeur):
    from .models import Parametre
    p = db.session.get(Parametre, cle)
    if not p:
        p = Parametre(cle=cle)
        db.session.add(p)
    p.valeur = str(valeur)


def tva_taux():
    if get_param("tva_active", "0") != "1":
        return 0.0
    return to_float(get_param("tva_taux", "18")) / 100.0


# ── Numérotation ──
def next_numero(model, prefix, d=None):
    d = d or datetime.now()
    base = f"{prefix}-{d.strftime('%y%m')}-"
    last = db.session.query(func.max(model.numero)).filter(model.numero.like(base + "%")).scalar()
    n = int(last.split("-")[-1]) + 1 if last else 1
    return f"{base}{n:04d}"


# ── Audit ──
def audit(action, entite, entite_id=None, details=None):
    from .models import AuditLog
    uid = None
    try:
        if current_user and current_user.is_authenticated:
            uid = current_user.id
    except Exception:
        uid = None
    if isinstance(details, (dict, list)):
        details = json.dumps(details, ensure_ascii=False, default=str)
    db.session.add(AuditLog(user_id=uid, action=action, entite=entite, entite_id=entite_id, details=details))


def current_uid():
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


# ── Jetons anti double-soumission ──
def new_form_token():
    return secrets.token_hex(16)


def consume_form_token(token):
    """Retourne False si le formulaire a déjà été soumis."""
    from .models import SubmissionToken
    if not token:
        return True  # formulaires sans jeton (API interne / tests)
    if db.session.get(SubmissionToken, token):
        return False
    db.session.add(SubmissionToken(token=token))
    return True
