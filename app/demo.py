"""Identification et suppression des données de démonstration."""
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from .extensions import db

_registered = False


def register_demo_listener():
    global _registered
    if _registered:
        return
    _registered = True

    @event.listens_for(Session, "before_flush")
    def _mark_demo(session, ctx, instances):
        if not session.info.get("demo"):
            return
        for obj in session.new:
            if hasattr(obj, "demo"):
                obj.demo = True


class mode_demo:
    """with mode_demo(): ... → tout objet créé est marqué demo=True."""
    def __enter__(self):
        db.session.info["demo"] = True

    def __exit__(self, *a):
        db.session.info["demo"] = False


def nb_demo():
    from .models import Vente, Client, Lot
    return {"ventes": Vente.query.filter_by(demo=True).count(), "clients": Client.query.filter_by(demo=True).count(),
            "lots": Lot.query.filter_by(demo=True).count()}


def purger_demo():
    """Supprime toutes les lignes demo=True, dans l'ordre inverse des dépendances.
    Échoue proprement (rollback) si des opérations réelles dépendent de données de démo."""
    from .models import AuditLog, Periode, Ecriture
    tables = [t for t in reversed(db.metadata.sorted_tables) if "demo" in t.c]
    try:
        # Les écritures de contre-passation se référencent entre elles
        db.session.execute(Ecriture.__table__.update().where(Ecriture.__table__.c.demo.is_(True)).values(
            contre_passation_de=None))
        # Lots enfants (transferts, retours) avant parents
        lot = db.metadata.tables["lot"]
        db.session.execute(lot.update().where(lot.c.demo.is_(True)).values(lot_parent_id=None))
        for tname, col in (("commande", "vente_id"), ("transformation", "lot_source_id")):
            t = db.metadata.tables[tname]
            db.session.execute(t.update().where(t.c.demo.is_(True)).values({col: None}))
        for t in tables:
            db.session.execute(t.delete().where(t.c.demo.is_(True)))
        # Périodes ouvertes vides
        db.session.commit()
    except Exception as e:  # noqa
        db.session.rollback()
        raise RuntimeError("Impossible de supprimer les données de démo : des opérations réelles y font référence "
                           f"({e.__class__.__name__}).")
