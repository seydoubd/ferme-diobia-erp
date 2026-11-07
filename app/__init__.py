import os
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.engine import Engine
from .extensions import db, login_manager, csrf
from .utils import fcfa, nombre, date_fr, new_form_token, get_param, MOIS_FR, tva_taux


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


def create_app(config=None):
    serverless = bool(os.environ.get("VERCEL"))
    # Sur Vercel, seul /tmp est inscriptible : la base doit être PostgreSQL (DATABASE_URL)
    app = Flask(__name__, instance_relative_config=True,
                instance_path="/tmp/diobia-instance" if serverless else None)
    app.url_map.strict_slashes = False
    os.makedirs(app.instance_path, exist_ok=True)
    db_url = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
              or "sqlite:///" + os.path.join(app.instance_path, "diobia.db"))
    # Pilote PostgreSQL explicite (psycopg2), quel que soit le format d'URL fourni par l'hébergeur
    for prefix in ("postgres://", "postgresql://"):
        if db_url.startswith(prefix):
            db_url = "postgresql+psycopg2://" + db_url[len(prefix):]
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or (_secret_from(db_url) if serverless else _secret(app.instance_path)),
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER") or os.path.join(app.instance_path, "justificatifs"),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        WTF_CSRF_TIME_LIMIT=None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    if config:
        app.config.update(config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa
    from .demo import register_demo_listener
    register_demo_listener()

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(models.User, int(uid))

    app.jinja_env.filters.update(fcfa=fcfa, nombre=nombre, date_fr=date_fr)
    app.jinja_env.globals.update(MOIS_FR=MOIS_FR, now=datetime.now, today=date.today)

    @app.before_request
    def acces_direct():
        """Mode sans connexion (par défaut) : ouverture directe en administrateur.
        La connexion obligatoire se réactive dans Paramètres."""
        if request.endpoint in (None, "static", "manifest", "sw") or current_user.is_authenticated:
            return
        if get_param("connexion_obligatoire", "0") == "1":
            return
        from flask_login import login_user
        u = models.User.query.filter_by(role="admin", actif=True).order_by(models.User.id).first()
        if not u:
            import secrets
            u = models.User(nom="Administrateur", email="admin@fermediobia.sn", role="admin")
            u.set_password(secrets.token_urlsafe(24))
            db.session.add(u)
            db.session.commit()
        login_user(u, remember=True)

    @app.context_processor
    def ctx():
        from .permissions import ROLES
        return {"can": (lambda p: current_user.is_authenticated and current_user.can(p)),
                "form_token": new_form_token, "ROLES": ROLES,
                "nom_entreprise": get_param("nom_entreprise", "La Ferme Diobia"),
                "demo_active": get_param("demo_chargee", "0") == "1", "get_tva": tva_taux(),
                "connexion_obligatoire": get_param("connexion_obligatoire", "0") == "1"}

    from .routes import register_blueprints
    register_blueprints(app)

    @app.route("/manifest.json")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.json", mimetype="application/manifest+json")

    @app.route("/sw.js")
    def sw():
        r = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        r.headers["Service-Worker-Allowed"] = "/"
        r.headers["Cache-Control"] = "no-cache"
        return r

    @app.errorhandler(403)
    def e403(e):
        return render_template("erreur.html", code=403, titre="Accès refusé",
                               message="Vous n'avez pas les droits nécessaires pour cette action."), 403

    @app.errorhandler(404)
    def e404(e):
        return render_template("erreur.html", code=404, titre="Page introuvable",
                               message="La page demandée n'existe pas."), 404

    @app.errorhandler(500)
    def e500(e):
        db.session.rollback()
        return render_template("erreur.html", code=500, titre="Erreur interne",
                               message="Une erreur est survenue. Aucune donnée partielle n'a été enregistrée."), 500

    from .cli import register_cli
    register_cli(app)

    with app.app_context():
        db.create_all()
        from .seed import init_base
        init_base(creer_admin=not serverless or bool(os.environ.get("ADMIN_PASSWORD")))
    return app


def _secret_from(db_url):
    """Clé stable dérivée de l'URL de la base (secrète) quand SECRET_KEY n'est pas fournie (Vercel)."""
    import hashlib
    return hashlib.sha256(("diobia::" + db_url).encode()).hexdigest()


def _secret(path):
    f = os.path.join(path, ".secret_key")
    if os.path.exists(f):
        return open(f).read().strip()
    import secrets
    k = secrets.token_hex(32)
    with open(f, "w") as fh:
        fh.write(k)
    return k
