import click
from .extensions import db


def register_cli(app):
    @app.cli.command("demo")
    def demo():
        """Charge les données de démonstration."""
        from .seed import seed_demo
        seed_demo()
        click.echo("Données de démonstration chargées.")

    @app.cli.command("purge-demo")
    def purge():
        """Supprime les données de démonstration."""
        from .demo import purger_demo
        purger_demo()
        click.echo("Données de démonstration supprimées.")

    @app.cli.command("admin")
    @click.option("--email", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def admin(email, password):
        """Crée ou réinitialise un administrateur."""
        from .models import User
        u = User.query.filter_by(email=email.lower()).first() or User(email=email.lower(), nom="Administrateur")
        u.role, u.actif = "admin", True
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Administrateur {u.email} prêt.")
